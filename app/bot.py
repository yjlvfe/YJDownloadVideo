import asyncio, secrets, logging, subprocess, os
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from .config import settings, ffmpeg_location
from .db import init_db, SessionLocal, Job, utcnow
from .security import validate_public_url
from .worker import process_job
from .status import read_status, clear_status, load_user_prefs, save_user_prefs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# على Vercel لا يوجد عملية polling دائمة — يعمل عبر Webhook وتنفيذ فوري داخل الطلب
WEBHOOK_MODE = bool(os.environ.get("VERCEL")) or settings.vercel_mode

PREFS = load_user_prefs()  # user_id(str) -> "audio" | "video"

def _pref_mode(uid: int) -> str:
    return PREFS.get(str(uid), "video")

def _set_pref(uid: int, mode: str):
    PREFS[str(uid)] = mode
    save_user_prefs(PREFS)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في YJDownloadVideo 🤖\n\n"
        "أرسل أي رابط يحتوي فيديو — من أي موقع في العالم: سوشيال ميديا، صفحات عادية، "
        "مشغّلات JS، روابط مباشرة… وسأحمّله لك فورًا.\n\n"
        "🔐 للمواقع اللي تطلب تسجيل دخول: أرسل ملف الكوكيز (من إضافة Get cookies.txt "
        "في متصفحك) مرة واحدة — وبعدها كل روابطك تتحمّل بجلستك.\n"
        "🧹 /clearcookies لحذف الكوكيز المحفوظة.\n\n"
        "📦 الملفات الكبيرة حتى 2GB تصل مباشرة عبر البوت (خادم Bot API محلي).\n"
        "لأي ملف نادر يتجاوز 2GB: /link2gb لربط حسابك الشخصي.\n\n"
        "🎵 /audio — المقاطع القادمة تُرسل كصوت فقط\n"
        "🎬 /video — العودة لإرسال الفيديو (الوضع الافتراضي)\n\n"
        "الاستثناء الوحيد: البث المشفّر بالكامل (DRM) لا يمكن فكّه."
    )

# حالات تسجيل الدخول: user_id -> {"step": "phone"|"code"|"2fa", "phone": str}
LOGIN_STATE: dict[int, dict] = {}

async def link2gb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from .mtproto import has_session
    uid = update.effective_user.id
    if has_session(uid):
        await update.message.reply_text("✅ حسابك مربوط مسبقًا — الملفات الكبيرة ستُرسل عبره تلقائيًا.\n/unlink2gb لفصله.")
        return
    LOGIN_STATE[uid] = {"step": "phone", "phone": ""}
    await update.message.reply_text(
        "📱 لربط حسابك الشخصي (لإرسال حتى 2GB):\n"
        "أرسل رقم هاتف التليجرام بصيغة دولية، مثال: +9665XXXXXXXX\n\n"
        "⚠️ تنبيه: الاستخدام المكثف الآلي لحساب شخصي قد يعرّضه لقيود من تيليجرام — استخدمه باعتدال."
    )

async def unlink2gb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from .mtproto import logout
    uid = update.effective_user.id
    LOGIN_STATE.pop(uid, None)
    await logout(uid)
    await update.message.reply_text("🧹 تم فصل حسابك الشخصي. ستعود الملفات الكبيرة لروابط التحميل.")

async def handle_login_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """يعالج رسائل الهاتف/الكود/كلمة المرور أثناء ربط الحساب. يعيد True إذا استهلك الرسالة."""
    from .mtproto import start_login, complete_login, complete_2fa
    uid = update.effective_user.id
    state = LOGIN_STATE.get(uid)
    if not state:
        return False
    text = (update.message.text or "").strip()
    if state["step"] == "phone":
        res = await start_login(uid, text)
        if res == "already":
            LOGIN_STATE.pop(uid, None)
            await update.message.reply_text("✅ حسابك مربوط مسبقًا.")
        elif res == "code_sent":
            state["phone"], state["step"] = text, "code"
            await update.message.reply_text("🔑 وصلك رمز تحقق في تيليجرام — أرسله هنا.")
        elif res == "bad_phone":
            await update.message.reply_text("⚠️ رقم الهاتف غير صالح. أرسله بصيغة دولية: +9665XXXXXXXX")
        else:
            await update.message.reply_text(f"❌ {res}")
        return True
    if state["step"] == "code":
        res = await complete_login(uid, state["phone"], text)
        if res == "ok":
            LOGIN_STATE.pop(uid, None)
            await update.message.reply_text("✅ تم الربط بنجاح! الآن أي ملف أكبر من 50MB سيُرسل عبر حسابك حتى 2GB.")
        elif res == "2fa":
            state["step"] = "2fa"
            await update.message.reply_text("🔐 حسابك مفعّل عليه التحقق بخطوتين — أرسل كلمة المرور.")
        elif res == "bad_code":
            await update.message.reply_text("⚠️ الرمز غير صحيح أو منتهي — أرسله مجددًا.")
        else:
            await update.message.reply_text(f"❌ {res}")
        return True
    if state["step"] == "2fa":
        res = await complete_2fa(uid, text)
        if res == "ok":
            LOGIN_STATE.pop(uid, None)
            await update.message.reply_text("✅ تم الربط بنجاح! الآن أي ملف أكبر من 50MB سيُرسل عبر حسابك حتى 2GB.")
        elif res == "bad_password":
            await update.message.reply_text("⚠️ كلمة المرور غير صحيحة — أعد المحاولة.")
        else:
            await update.message.reply_text(f"❌ {res}")
        return True
    return False

async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يستقبل ملف كوكيز Netscape ويحفظه للمستخدم."""
    doc = update.message.document
    if not doc:
        return
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".txt") or "cookie" in fname):
        await update.message.reply_text("⚠️ أرسل ملف الكوكيز بصيغة .txt (من إضافة Get cookies.txt).")
        return
    user_key = f"tg:{update.effective_user.id}"
    try:
        f = await doc.get_file()
        dest = settings.cookies_path / f"{user_key}.txt"
        await f.download_to_drive(str(dest))
        if dest.stat().st_size > 2 * 1024 * 1024:
            dest.unlink(missing_ok=True)
            await update.message.reply_text("⚠️ الملف كبير جدًا (الحد 2MB).")
            return
    except Exception as e:  # noqa: BLE001
        log.exception("cookie save failed")
        await update.message.reply_text(f"❌ فشل حفظ الكوكيز: {str(e)[:150]}")
        return
    await update.message.reply_text(
        "✅ تم حفظ الكوكيز بنجاح.\n"
        "الآن أرسل أي رابط من ذلك الموقع وسيُحمَّل بجلستك المسجّلة."
    )

async def clear_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = f"tg:{update.effective_user.id}"
    p = settings.cookies_path / f"{user_key}.txt"
    if p.exists():
        p.unlink()
        await update.message.reply_text("🧹 تم حذف الكوكيز المحفوظة.")
    else:
        await update.message.reply_text("لا توجد كوكيز محفوظة.")

async def audio_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _set_pref(update.effective_user.id, "audio")
    await update.message.reply_text("🎵 تم تفعيل وضع الصوت — المقاطع القادمة ستُرسل كصوت فقط.\n🎬 /video للعودة للفيديو.")

async def video_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _set_pref(update.effective_user.id, "video")
    await update.message.reply_text("🎬 وضع الفيديو مفعل — المقاطع القادمة ستُرسل كفيديو كامل.")

async def on_mp3_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر MP3 🎧 تحت المقطع: يحوّل الفيديو إلى MP3 ويرسله."""
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("mp3:"):
        await query.answer()
        return
    jid = data[4:]
    user_id = query.from_user.id if query.from_user else None

    with SessionLocal() as db:
        j = db.get(Job, jid)
        if not j or not j.file_path or not Path(j.file_path).is_file():
            await query.answer("❌ الملف لم يعد متوفرًا (انتهت صلاحيته).", show_alert=True)
            return
        owner = j.owner_key or ""
        if owner.startswith("tg:") and str(user_id) != owner[3:]:
            await query.answer("❌ هذا الزر لصاحب الطلب فقط.", show_alert=True)
            return
        path = Path(j.file_path)
        title = j.title or "مقطع"
        chat_id = query.message.chat_id if query.message else None
    if not chat_id:
        await query.answer()
        return

    await query.answer("🎧 جارٍ التحويل إلى MP3…")
    bot = _bot_instance()
    status_msg = None
    try:
        # فحص وجود صوت أولًا — بعض المقاطع (تيك توك أحيانًا) بدون صوت
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if not probe.stdout.strip():
            await query.answer("🔇 هذا المقطع بدون صوت — لا يمكن تحويله لـ MP3.", show_alert=True)
            return
        status_msg = await bot.send_message(chat_id, "🎧 جارٍ التحويل إلى MP3…")
        out = path.with_suffix(".mp3")
        ffmpeg = ffmpeg_location()
        # تحويل الصوت فقط بجودة عالية
        def convert():
            subprocess.run(
                [ffmpeg, "-y", "-i", str(path), "-vn", "-codec:a", "libmp3lame",
                 "-q:a", "2", "-map_metadata", "0", str(out)],
                capture_output=True, timeout=600, check=True,
            )
        await asyncio.to_thread(convert)
        if not out.is_file() or out.stat().st_size == 0:
            raise RuntimeError("فشل التحويل")
        await bot.send_chat_action(chat_id, "upload_audio")
        from telegram import InputFile
        with open(out, "rb") as f:
            await bot.send_audio(chat_id, InputFile(f, read_file_handle=False),
                                 caption=f"🎵 {title[:1024]}")
        if status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        log.exception("mp3 conversion failed")
        text = f"❌ تعذر التحويل إلى MP3: {str(e)[:150]}"
        if status_msg is not None:
            try:
                await status_msg.edit_text(text)
            except Exception:  # noqa: BLE001
                await bot.send_message(chat_id, text)
        else:
            await bot.send_message(chat_id, text)


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # رسائل تسجيل الدخول (هاتف/كود/2FA) تستهلك أولًا
    if await handle_login_text(update, context):
        return
    url = (update.message.text or "").strip()
    user_key = f"tg:{update.effective_user.id}"
    try:
        validate_public_url(url)
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    with SessionLocal() as db:
        # طابور: عدد مهامك النشطة = موقعك في الطابور (لا رفض — الكل ينتظر دوره)
        queue_pos = db.query(Job).filter(
            Job.owner_key == user_key,
            Job.status.in_(["waiting", "downloading", "preparing"]),
        ).count() + 1
        j = Job(
            id=secrets.token_urlsafe(16), url=url,
            requested_format="audio" if _pref_mode(update.effective_user.id) == "audio" else "best",
            status="waiting", owner_key=user_key, source="telegram",
        )
        db.add(j); db.commit(); jid = j.id

    mode = _pref_mode(update.effective_user.id)
    if queue_pos == 1:
        await update.message.reply_text(f"📥 تم استلام الرابط، جارٍ المعالجة… ({'🎵 صوت' if mode == 'audio' else '🎬 فيديو'})")
    else:
        await update.message.reply_text(f"📥 تمت إضافة الرابط إلى الطابور — موضعك #{queue_pos} ({'🎵 صوت' if mode == 'audio' else '🎬 فيديو'})")
    if WEBHOOK_MODE:
        # على Vercel: التنفيذ فوري داخل الطلب (لا توجد عمليات خلفية دائمة)
        await watch_and_deliver(update.effective_chat.id, jid)
    else:
        asyncio.create_task(watch_and_deliver(update.effective_chat.id, jid))

def _make_request(progress_cb=None):
    """مهلات طويلة + تتبع تقدم الرفع (PTB v21 حذف معامل progress من دوال الإرسال)."""
    from .progress_request import ProgressHTTPXRequest
    return ProgressHTTPXRequest(
        progress_cb=progress_cb,
        read_timeout=600, write_timeout=600, connect_timeout=30, pool_timeout=30,
    )


def _bot_instance(progress_cb=None):
    """نسخة Bot بالخادم المحلي (إن وُجد) — مهم جدًا: بدون base_url يرسل للسحابة ويرفض فوق 50MB بـ 413."""
    from telegram import Bot
    kwargs = dict(token=settings.telegram_bot_token, request=_make_request(progress_cb))
    if settings.telegram_bot_api_base:
        base = settings.telegram_bot_api_base.rstrip("/")
        kwargs["base_url"] = f"{base}/bot"
    return Bot(**kwargs)


def _progress_bar(pct: int, width: int = 12) -> str:
    """شريط تقدم نصي: [██████░░░░░░] 50%"""
    pct = max(0, min(100, int(pct)))
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


async def _edit_progress(msg, text: str):
    """يعدّل رسالة التقدم بأمان (قد تُحذف أو يفشل التعديل — لا ننهار)."""
    try:
        return await msg.edit_text(text)
    except Exception:  # noqa: BLE001
        return msg


async def watch_and_deliver(chat_id: int, jid: str):
    """رسالة شريط تقدم واحدة: تحميل 0→100% ← رفع 0→100% ← الملف. بلا إزعاج رسائل."""
    up_state = {"pct": 0}

    def sync_up_cb(current: int, total: int):
        up_state["pct"] = int(current / total * 100) if total else 0

    bot = _bot_instance(progress_cb=sync_up_cb)
    bar = None
    try:
        bar = await bot.send_message(chat_id, f"⬇️ جارٍ التحميل [{_progress_bar(0)}] 0%")
    except Exception:  # noqa: BLE001
        pass

    last_pct = -1
    task = asyncio.create_task(asyncio.to_thread(process_job, jid))
    while not task.done():
        st = read_status(jid)
        if st and st.get("stage") in ("downloading", "preparing"):
            pct = st.get("pct", 0)
            if pct >= last_pct + 10 and bar is not None:
                if st["stage"] == "preparing":
                    bar = await _edit_progress(bar, "🔧 جارٍ دمج الصوت والفيديو…")
                else:
                    bar = await _edit_progress(bar, f"⬇️ جارٍ التحميل [{_progress_bar(pct)}] {pct}%")
                last_pct = pct
        await asyncio.sleep(2)
    try:
        await task
    except Exception as e:  # noqa: BLE001
        log.exception("worker failed")
        if bar is not None:
            bar = await _edit_progress(bar, f"❌ خطأ أثناء المعالجة: {str(e)[:200]}")

    with SessionLocal() as db:
        j = db.get(Job, jid)
        state = j.status if j else "failed"
        path, size, token, title = (j.file_path, j.file_size, j.download_token, j.title) if j else (None, None, None, None)
        err = j.error if j else None
        is_audio = (j.requested_format == "audio") if j else False

    if state == "ready" and path and size is not None:
        if size > settings.telegram_send_limit_bytes:
            # أكبر من 2GB — لا يمكن إرساله إطلاقًا
            if bar is not None:
                bar = await _edit_progress(bar, f"✅ تم التحميل ({size / 1024 / 1024:.0f}MB) — يتجاوز حد 2GB.\nرابط التحميل: {settings.base_url}/download/{token}")
            else:
                await bot.send_message(chat_id, f"✅ تم التحميل ({size / 1024 / 1024:.0f}MB) — يتجاوز حد 2GB.\nرابط التحميل: {settings.base_url}/download/{token}")
            return
        # ← مرحلة الرفع: نفس الرسالة تتحول لشريط رفع
        if bar is not None:
            bar = await _edit_progress(bar, f"✅ تم التحميل ({size / 1024 / 1024:.0f}MB) — ⬆️ جارٍ الرفع [{_progress_bar(0)}] 0%")
        up_state["pct"] = 0
        caption = (title or "الفيديو")[:1024]

        async def do_send():
            # InputFile(read_file_handle=False): يبقي الملف متدفقًا (بدل قراءته كاملًا في الذاكرة)
            # — ضروري لتتبع التقدم ولتفادي استهلاك 2GB رام للملفات الكبيرة
            from telegram import InputFile
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("MP3 🎧", callback_data=f"mp3:{jid}")]])
            if is_audio:
                with open(path, "rb") as f:
                    await bot.send_audio(chat_id, InputFile(f, read_file_handle=False), caption=f"🎵 {caption}")
            else:
                with open(path, "rb") as f:
                    await bot.send_video(chat_id, InputFile(f, read_file_handle=False), caption=caption, reply_markup=kb)

        send_task = asyncio.create_task(do_send())
        last_up = -1
        while not send_task.done():
            pct = up_state["pct"]
            if pct >= last_up + 10 and bar is not None:
                bar = await _edit_progress(bar, f"⬆️ جارٍ الرفع [{_progress_bar(pct)}] {pct}%")
                last_up = pct
            await asyncio.sleep(2)
        try:
            await send_task
        except Exception as e:  # noqa: BLE001
            log.exception("send failed, falling back to link")
            if bar is not None:
                bar = await _edit_progress(bar, f"⚠️ تعذر الإرسال المباشر ({str(e)[:100]}).\nرابط التحميل: {settings.base_url}/download/{token}")
            else:
                await bot.send_message(chat_id, f"⚠️ تعذر الإرسال المباشر ({str(e)[:100]}).\nرابط التحميل: {settings.base_url}/download/{token}")
            return
        # أناقة احترافية: يُحذف شريط التقدم ويبقى المقطع فقط
        if bar is not None:
            try:
                await bar.delete()
            except Exception:  # noqa: BLE001
                pass
        clear_status(jid)
        return
    elif state == "failed":
        reason = ""
        if err:
            low = err.lower()
            if any(k in low for k in ("drm", "widevine", "playready", "fairplay", "license", "encrypt")):
                reason = "\n🔒 السبب: هذا المحتوى مشفّر بـ DRM — لا يمكن تحميله (نظام حماية قانوني)."
            elif any(k in low for k in ("login", "auth", "cookie", "403", "forbidden", "membership", "subscription", "premium", "sign in")):
                reason = f"\n🔐 السبب: الموقع يطلب تسجيل دخول/اشتراك. الحل: حمّل بملف كوكيز جلستك (أرسل الملف مع الرابط).\nالتفاصيل: {err[:200]}"
            elif any(k in low for k in ("live", "بث")):
                reason = "\n📡 السبب: هذا بث مباشر وليس مقطعًا مسجلًا."
            else:
                reason = f"\n📋 التفاصيل التقنية: {err[:300]}"
        if bar is not None:
            bar = await _edit_progress(bar, f"❌ فشلت المعالجة.{reason}\n\nأرسل الرابط هنا وسأشخّصه لك.")
        else:
            await bot.send_message(chat_id, f"❌ فشلت المعالجة.{reason}\n\nأرسل الرابط هنا وسأشخّصه لك.")

def build_application() -> Application:
    """يبني تطبيق البوت (مشترك بين polling و Webhook)."""
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in .env")
    init_db()
    builder = Application.builder().token(settings.telegram_bot_token).request(_make_request())
    # خادم Bot API محلي (إن وُجد): يرفع حد الإرسال إلى 2000MB ويُزيل حد التحميل
    if settings.telegram_bot_api_base and not WEBHOOK_MODE:
        base = settings.telegram_bot_api_base.rstrip("/")
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot")
        log.info("using local Bot API server: %s", base)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clearcookies", clear_cookies))
    app.add_handler(CommandHandler("link2gb", link2gb))
    app.add_handler(CommandHandler("unlink2gb", unlink2gb))
    app.add_handler(CommandHandler("audio", audio_mode))
    app.add_handler(CommandHandler("video", video_mode))
    app.add_handler(CallbackQueryHandler(on_mp3_button, pattern=r"^mp3:"))
    # ملاحظة: filters.Document كلاس في PTB v21 ويكسر المعالج — استخدم filters.ATTACHMENT (نسخة جاهزة)
    app.add_handler(MessageHandler(filters.ATTACHMENT, receive_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive))
    return app


_webhook_app: Application | None = None


async def webhook_bot():
    """نسخة البوت المستخدمة في وضع Webhook (تُبنى مرة واحدة)."""
    global _webhook_app
    if _webhook_app is None:
        _webhook_app = build_application()
        await _webhook_app.initialize()
    return _webhook_app.bot


async def handle_update_webhook(update: Update) -> None:
    """يعالج تحديثًا قادمًا من Webhook تيليجرام (وضع Vercel)."""
    global _webhook_app
    if _webhook_app is None:
        _webhook_app = build_application()
        await _webhook_app.initialize()
    await _webhook_app.process_update(update)


def main():
    app = build_application()
    log.info("bot started, polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
