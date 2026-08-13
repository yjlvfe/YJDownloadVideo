import asyncio, secrets, logging, os, time, shutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from .config import settings
from .db import init_db, SessionLocal, Job, utcnow
from .security import validate_public_url
from .worker import process_job, _owner_cookies
from .status import read_status, clear_status, load_user_prefs, save_user_prefs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

# على Vercel لا يوجد عملية polling دائمة — يعمل عبر Webhook وتنفيذ فوري داخل الطلب
WEBHOOK_MODE = bool(os.environ.get("VERCEL")) or settings.vercel_mode

PREFS = load_user_prefs()  # user_id(str) -> "ask" | "audio" | "video"

# مراجع قوية لمهام الخلفية — بدونها قد تُجمع المهمة (GC) في منتصف التسليم فتُلغى صامتة
_TASKS: set = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.full_name
    await update.message.reply_text(
        f"أهلاً بك {user_name} 🤖\n\n"
        "أرسل أي رابط فيديو — من أي موقع — وسأحمّله لك فورًا.\n\n"
        "🎯 عند إرسال الرابط تظهر كل الجودات مباشرة (الصوت في الأخير).\n"
        "🔄 /ip — تبديل عنوان التحميل يدويًا (لو يوتيوب حظر الـ IP الحالي).\n\n"
        "الاستثناء الوحيد: البث المشفّر بالكامل (DRM) لا يمكن فكّه."
    )


def _read_ip() -> str:
    """قراءة IP الخروج الحالي من ملف التحكم المشترك."""
    import os
    p = "/app/vpn-ctl/current_ip.txt"
    try:
        if os.path.exists(p):
            return open(p).read().strip() or "غير معروف"
    except Exception:
        pass
    return "غير معروف"


def _ask_rotate() -> bool:
    """طلب تبديل — يكتب العلم للمراقب على الهوست. يرجع False عند الفشل."""
    import os
    try:
        open("/app/vpn-ctl/rotate.now", "w").write("1")
        return True
    except Exception:
        return False


async def ip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض الـ IP الحالي + زرّي تغيير/إلغاء."""
    cur = _read_ip()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 تغيير", callback_data="ip:change"),
        InlineKeyboardButton("❌ إلغاء", callback_data="ip:cancel"),
    ]])
    await update.message.reply_text(
        f"🌐 الـ IP الحالي للتحميل:\n\n<code>{cur}</code>\n\n"
        "اضغط تغيير لجلب عنوان جديد.",
        reply_markup=kb, parse_mode="HTML")


async def on_ip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة زرّي تغيير/إلغاء — مع ضمان تغيير حقيقي (إعادة المحاولة تلقائيًا)."""
    import os
    query = update.callback_query
    data = query.data or ""
    await query.answer()
    if data == "ip:cancel":
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    if data != "ip:change":
        return

    old_ip = _read_ip()
    ctl = "/app/vpn-ctl"
    cur = os.path.join(ctl, "current_ip.txt")

    # تحديث الرسالة: جاري التغيير
    try:
        await query.message.edit_text(
            f"🔄 جاري تغيير الـ IP...\n\nالـ IP الحالي: <code>{old_ip}</code>",
            parse_mode="HTML")
    except Exception:
        pass

    import asyncio
    MAX_TRIES = 6
    for attempt in range(1, MAX_TRIES + 1):
        if not _ask_rotate():
            await query.message.edit_text("❌ تعذر طلب التبديل — تحقق من السيرفر.")
            return
        # انتظر معالجة المراقب (10 ثوانٍ دورة) + إعادة الاتصال
        new_ip = old_ip
        for _ in range(20):  # حتى ~100 ثانية لكل محاولة
            await asyncio.sleep(5)
            # علم ما زال موجودًا؟ المراقب لسه ما عالج
            if not os.path.exists(os.path.join(ctl, "rotate.now")):
                new_ip = _read_ip()
                if new_ip != old_ip and new_ip != "غير معروف":
                    break
        if new_ip != old_ip and new_ip != "غير معروف":
            try:
                await query.message.edit_text(
                    f"✅ تم التغيير!\n\nالـ IP القديم: <code>{old_ip}</code>\n"
                    f"الـ IP الجديد: <code>{new_ip}</code>",
                    parse_mode="HTML")
            except Exception:
                pass
            return
        # لم يتغير — أعد المحاولة
        try:
            await query.message.edit_text(
                f"🔁 لم يتغير الـ IP (محاولة {attempt}/{MAX_TRIES}) — أعيد المحاولة...",
                parse_mode="HTML")
        except Exception:
            pass

    try:
        await query.message.edit_text(
            "⚠️ تعذر تغيير الـ IP بعد عدة محاولات — جرّب لاحقًا.", parse_mode="HTML")
    except Exception:
        pass


# تفضيلات المستخدم: "ask" (افتراضي — اختيار عند كل رابط) | "audio" | "video"
def _pref_mode(uid: int) -> str:
    return PREFS.get(str(uid), "ask")


def _set_pref(uid: int, mode: str):
    PREFS[str(uid)] = mode
    save_user_prefs(PREFS)


async def reset_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _set_pref(update.effective_user.id, "ask")
    await update.message.reply_text(
        "🔄 تمت الإعادة — سيظهر لك الاختيار (فيديو/صوت/دقة) عند كل رابط."
    )


async def _create_job(user_id: int, chat_id: int, url: str, fmt: str, context,
                      status_msg=None) -> str | None:
    """ينشئ مهمة ويبدأ تنفيذها فورًا — بلا طابور، كل شي يتحمل دفعة واحدة.

    2026-08: معالجة متوازية في الـ worker (ThreadPoolExecutor=20) — تنفيذ
    فوري بدون انتظار، البوت يعرض التحميل مباشرة دون رسائل طابور."""
    user_key = f"tg:{user_id}"
    with SessionLocal() as db:
        active = db.query(Job).filter(
            Job.owner_key == user_key,
            Job.status.in_(["waiting", "downloading", "preparing"]),
        ).count()
        # 🛡️ الحد الفعلي لكل مستخدم
        if active >= settings.max_active_jobs_per_telegram_user:
            try:
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ لديك {active} عمليات نشطة (الحد {settings.max_active_jobs_per_telegram_user}). "
                    "انتظر اكتمال بعضها ثم أرسل الرابط مجددًا.")
            except Exception:  # noqa: BLE001
                pass
            return None
        job = Job(id=secrets.token_urlsafe(16), url=url, requested_format=fmt,
                  status="waiting", owner_key=user_key, source="telegram")
        db.add(job)
        db.commit()
        jid = job.id
    # لا رسائل تأكيد منفصلة — رسالة الحالة الواحدة تتكفل بكل شيء (قرار المستخدم 2026-08)
    if WEBHOOK_MODE:
        # Vercel: لا Redis — تنفيذ فوري داخل العملية (خيط) ثم مراقبة
        _spawn(asyncio.to_thread(process_job, jid))
        await watch_and_deliver(chat_id, jid, status_msg=status_msg)
    else:
        try:
            import redis
            redis.Redis.from_url(settings.redis_url).rpush('yjdownload:jobs', jid)
        except Exception:  # noqa: BLE001
            log.exception("redis push failed")
            with SessionLocal() as db:
                row = db.get(Job, jid)
                if row:
                    row.status = "failed"
                    row.error = "النظام مشغول حاليًا — حاول بعد قليل"
                    db.commit()
            try:
                await context.bot.send_message(chat_id, "⚠️ النظام مشغول حاليًا — حاول بعد قليل.")
            except Exception:  # noqa: BLE001
                pass
            return None
        _spawn(watch_and_deliver(chat_id, jid, status_msg=status_msg))
    return jid


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
        # 🛡️ فحص الحجم قبل التنزيل (كان يُنزَّل كاملًا ثم يُحذف — حتى 2GB كتابة مهدرة)
        if (f.file_size or 0) > 2 * 1024 * 1024:
            await update.message.reply_text("⚠️ الملف كبير جدًا (الحد 2MB).")
            return
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
    await update.message.reply_text("🎵 وضع الصوت مفعل — المقاطع القادمة تُرسل صوتًا مباشرة بدون سؤال.\n🔄 /reset للعودة للاختيار عند كل رابط.")

async def video_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _set_pref(update.effective_user.id, "video")
    await update.message.reply_text("🎬 وضع الفيديو مفعل — تُرسل الجودة الأفضل مباشرة بدون سؤال.\n🔄 /reset للعودة للاختيار عند كل رابط.")

# ============================================================
# نظام اختيار التحميل (فيديو/صوت/دقة) — بدل زر MP3
# ============================================================

PENDING: dict[str, dict] = {}      # nonce -> {user_id, chat_id, url, created, offered?}
_PICK_TTL = 300                     # 5 دقائق لاتخاذ القرار

_QUALITY_LABELS = {"best": "⭐ الأفضل", "audio": "🎵 صوت"}


def _quality_label(fmt: str) -> str:
    """اسم الجودة للرسائل: ثابتة معروفة أو دقة رقمية من الفحص الفعلي (مثل 2160 → 2160p)."""
    if fmt in _QUALITY_LABELS:
        return _QUALITY_LABELS[fmt]
    if fmt.isdigit():
        return f"{fmt}p"
    return fmt


def _offered_qualities(formats: list[dict], limit: int = 6) -> list[dict]:
    """دقات فريدة تنازليًا مع أحجامها من قائمة الجودات الحقيقية — أساس أزرار البوت."""
    seen: dict[int, dict] = {}
    for f in formats or []:
        h = f.get("height")
        if not h:
            continue
        try:
            h = int(h)
        except (ValueError, TypeError):
            continue
        size = f.get("size")
        cur = seen.get(h)
        if cur is None or (size or 0) > (cur.get("size") or 0):
            seen[h] = {"height": h, "size": size}
    return [seen[h] for h in sorted(seen, reverse=True)][:limit]


def _fmt_mb(size) -> str:
    """حجم مقروء داخل الزر: 1.5GB / 229MB / 11.3MB — بلا كسور زائدة."""
    if not size:
        return ""
    gb = size / 1073741824  # 1024³
    if gb >= 1:
        return f"{gb:.1f}GB"
    mb = size / 1048576
    if mb >= 100:
        return f"{mb:.0f}MB"
    return f"{mb:.1f}MB"


def _delete_media(file_path: str) -> None:
    """🗑️ حذف فوري مضمون للملف ومجلده بالكامل — لا يعتمد على كائن SQLAlchemy."""
    try:
        p = Path(file_path)
        p.unlink(missing_ok=True)
        shutil.rmtree(p.parent, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def delete_job_files(job) -> None:
    """🗑️ حذف فوري لملف المهمة ومجلدها وتحديث الصف (نقطة عدم التكدس)."""
    try:
        folder = Path(job.file_path).parent if job.file_path else None
        if job.file_path:
            Path(job.file_path).unlink(missing_ok=True)
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        with SessionLocal() as db:
            row = db.get(Job, job.id)
            if row:
                row.file_path = None
                row.file_size = None
                db.commit()
    except Exception:  # noqa: BLE001
        pass


def _cleanup_pending() -> None:
    now = time.time()
    for key in [k for k, v in PENDING.items() if now - v["created"] > _PICK_TTL]:
        PENDING.pop(key, None)


def _pick_kb(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=cb) for label, cb in row]
        for row in buttons
    ])


async def _ask_qualities(chat_id: int, user_id: int, url: str, context) -> None:
    """يعرض قائمة الجودات الكاملة مباشرة (بدون خطوة فيديو/صوت)."""
    _cleanup_pending()
    nonce = secrets.token_urlsafe(8)
    PENDING[nonce] = {"user_id": user_id, "chat_id": chat_id,
                      "url": url, "created": time.time()}
    
    # فحص الجودات المتاحة
    quals = []
    try:
        from .extractor import UniversalExtractor
        ex = UniversalExtractor(request_timeout=30)
        try:
            d = await asyncio.wait_for(
                asyncio.to_thread(ex.inspect, url), timeout=45)
        finally:
            ex.cleanup()
        quals = _offered_qualities(d.get("formats") or [])
    except Exception:
        pass
    
    # بناء الأزرار
    rows = []
    # الأفضل دائماً أول زر
    rows.append([("⭐ الأفضل", f"pick:q:{nonce}:best")])
    # الجودات حسب الارتفاع
    row = []
    for q in quals:
        label = f"{q['height']}p"
        if q.get('size'):
            label += f" · {_fmt_mb(q['size'])}"
        row.append((label, f"pick:q:{nonce}:{q['height']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # الصوت دائماً في الأخير
    audio_size = None
    if quals:
        audio_size = quals[-1].get("size")
    audio_label = "🎵 صوت" + (f" · {_fmt_mb(audio_size)}" if audio_size else "")
    rows.append([(audio_label, f"pick:a:{nonce}"), ("❌ إلغاء", f"pick:x:{nonce}")])
    
    kb = _pick_kb(rows)
    note = "" if quals else "\n⚠️ تعذر تحديد الجودات — سيُحمَّل الأفضل المتوفر."
    try:
        await context.bot.send_message(chat_id, "🎬 اختر الجودة:" + note, reply_markup=kb)
    except Exception:
        pass


async def on_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعالج اختيار المستخدم: فيديو ← جودات، صوت ← مباشرة، إلغاء."""
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("pick:"):
        await query.answer()
        return
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer("❌ اختيار غير صالح")
        return
    kind, nonce = parts[1], parts[2]
    pending = PENDING.get(nonce)
    if not pending:
        await query.answer("⏳ انتهى وقت الاختيار — أرسل الرابط مجددًا.", show_alert=True)
        return
    if pending["user_id"] != query.from_user.id:
        await query.answer("❌ هذا الاختيار لصاحب الطلب فقط.", show_alert=True)
        return
    if time.time() - pending['created'] > _PICK_TTL:
        PENDING.pop(nonce, None)
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer('⏳ انتهى وقت الاختيار — أرسل الرابط مجددًا.', show_alert=True)
        return

    if kind == "x":
        PENDING.pop(nonce, None)
        await query.answer("تم الإلغاء ✅")
        try:
            await query.message.delete()
        except Exception:  # noqa: BLE001
            pass
        return

    if kind == "v":
        # 🔍 فحص حقيقي: الجودات تُقرأ من المقطع نفسه — ممنوع جودات ثابتة/وهمية
        await query.answer()
        try:
            await query.message.edit_text("🔍 جارٍ فحص الجودات المتاحة في المقطع…")
        except Exception:  # noqa: BLE001
            pass
        heights: list[int] = []
        quals: list[dict] = []
        d: dict | None = None
        try:
            from .extractor import UniversalExtractor
            # 🍪 فحص الجودات يحتاج كوكيز المستخدم — بدونه تحجب المنصات IP الخادم
            user_key = f"tg:{pending['user_id']}"
            cookies_file = _owner_cookies(user_key)
            ex = UniversalExtractor(
                cookies_file=cookies_file,
                request_timeout=30,
                max_filesize=settings.max_file_size_bytes,
            )
            try:
                d = await asyncio.wait_for(
                    asyncio.to_thread(ex.inspect, pending["url"]), timeout=45)
            finally:
                ex.cleanup()
            quals = _offered_qualities(d.get("formats") or [])
        except Exception:  # noqa: BLE001
            quals = []
        heights = [q["height"] for q in quals]
        offered = {"best", *(str(h) for h in heights)}
        pending["offered"] = offered
        rows: list[list[tuple[str, str]]] = []
        if "best" in offered:
            # 📦 زر الأفضل يعرض الحجم المقدر (أكبر جودة في القائمة)
            best_size = quals[0].get("size") if quals else None
            best_label = "⭐ الأفضل" + (f" · {_fmt_mb(best_size)}" if best_size else "")
            rows.append([(best_label, f"pick:q:{nonce}:best")])
        row: list[tuple[str, str]] = []
        for q in quals:
            # 📦 كل جودة داخل زرها تعرض حجمها الحقيقي — إجباري بلا استثناء
            label = f"{q['height']}p" + (f" · {_fmt_mb(q['size'])}" if q.get("size") else "")
            row.append((label, f"pick:q:{nonce}:{q['height']}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        audio_size = d.get("audio_size") if d else None
        # إن لم يتوفر تدفق صوتي منفصل: قدر من أصغر فيديو مدمج (صوت+فيديو معًا)
        if not audio_size and quals:
            audio_size = quals[-1].get("size")  # أصغر دقة = الأقرب لحجم الصوت
        audio_label = "🎵 صوت" + (f" · {_fmt_mb(audio_size)}" if audio_size else "")
        rows.append([(audio_label, f"pick:a:{nonce}"), ("❌ إلغاء", f"pick:x:{nonce}")])
        kb = _pick_kb(rows)
        if heights:
            note = ""
        else:
            # 🩺 سبب فشل الفحص (إن وُجد) — يفيد المستخدم بدل رسالة عامة
            err_hint = (d or {}).get("_inspect_error", "") if d else ""
            note = f"\n⚠️ تعذر تحديد الجودات المتاحة.\n{err_hint}\nسيُحمَّل الأفضل المتوفر."
        try:
            await query.message.edit_text("🎬 اختر دقة الفيديو:" + note, reply_markup=kb)
        except Exception:  # noqa: BLE001
            pass
        return

    if kind == "a":
        fmt = "audio"
    elif kind == "q" and len(parts) >= 4:
        # الجودة يجب أن تكون مما عُرض فعلاً على المستخدم (من فحص المقطع)
        offered = pending.get("offered") or {"best"}
        if parts[3] not in offered:
            await query.answer("❌ هذه الجودة غير متاحة لهذا المقطع", show_alert=True)
            return
        fmt = parts[3]
    else:
        await query.answer("❌ اختيار غير صالح")
        return

    # 🎯 نفس رسالة الأزرار تتحول لشريط الحالة — لا رسائل جديدة ولا حذف (قرار المستخدم 2026-08)
    PENDING.pop(nonce, None)
    await query.answer("تم ✅")
    await _create_job(pending["user_id"], pending["chat_id"], pending["url"], fmt, context,
                      status_msg=query.message)


async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()
    try:
        validate_public_url(url)
    except ValueError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    mode = _pref_mode(update.effective_user.id)
    # مباشرة: عرض الجودات
    await _ask_qualities(update.effective_chat.id, update.effective_user.id, url, context)

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


def _progress_bar(pct: int, width: int = 16) -> str:
    """شريط تقدم نصي بتصميم احترافي: ▓▓▓▓▓▓▓░░░░░ 42%

    ▓ = مكتمل | ░ = متبقي — ceil يضمن عدم ظهور الشريط فارغًا أبدًا.
    العرض 16 حرف = مثالي لشاشات الجوال (لا ينكسر السطر)."""
    import math
    pct = max(0, min(100, int(pct)))
    filled = math.ceil(pct / 100 * width)
    if filled == 0 and pct > 0:
        filled = 1  # لا يظهر فارغًا إن كان هناك أي تقدم
    return "▓" * filled + "░" * (width - filled)


def _video_meta(path, outdir) -> dict:
    """مدة/أبعاد/صورة مصغرة حقيقية من الملف قبل الإرسال.

    🎬 ضروري: خادم Bot API المحلي لا يعالج الفيديو تلقائيًا — بدون هذه البيانات
    (duration=0 بلا معاينة) يعرض تيليجرام المقطع كملف (file) بدل فيديو.
    """
    import json
    import subprocess
    meta: dict = {}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration:format=duration",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout)
        st = (d.get("streams") or [{}])[0]
        w, h = st.get("width"), st.get("height")
        dur = st.get("duration") or d.get("format", {}).get("duration")
        if w and h:
            meta["width"], meta["height"] = int(w), int(h)
        if dur:
            meta["duration"] = int(float(dur))
    except Exception:  # noqa: BLE001
        pass
    try:
        dur = meta.get("duration") or 0
        at = min(30, max(1, dur // 4)) if dur else 2
        thumb = Path(outdir) / "thumb.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(path),
             "-frames:v", "1", "-vf", "scale=320:-2", str(thumb)],
            capture_output=True, timeout=30)
        if thumb.is_file() and thumb.stat().st_size > 0:
            meta["thumbnail"] = str(thumb)
    except Exception:  # noqa: BLE001
        pass
    return meta


async def _edit_progress(msg, text: str):
    """يعدّل رسالة التقدم بأمان (قد تُحذف أو يفشل التعديل — لا ننهار)."""
    try:
        return await msg.edit_text(text)
    except Exception:  # noqa: BLE001
        return msg


async def watch_and_deliver(chat_id: int, jid: str, status_msg=None):
    """رسالة حالة واحدة حية: تحميل % كل ثانية ← رفع % كل ثانية ← الملف ← تختفي.

    (2026-08: بلا طابور — الـ worker يعالج 20 مهمة متوازية، التحميل يبدأ فورًا.)"""
    up_state = {"pct": 0}

    def sync_up_cb(current: int, total: int):
        if total:
            up_state["pct"] = int(current / total * 100)
        elif current:
            # حجم غير معروف — يُحدث كل 5MB من progress_request
            up_state["pct"] = min(99, current // (5 * 1024 * 1024) * 5)

    bot = _bot_instance(progress_cb=sync_up_cb)
    try:
        bar = None
        if status_msg is not None:
            # نفس رسالة الأزرار تتحول لشريط الحالة
            bar = await _edit_progress(status_msg,
                                       f"⬇️ {_progress_bar(0)} 0% — جارٍ التحميل")
        if bar is None:
            try:
                bar = await bot.send_message(chat_id,
                                             f"⬇️ {_progress_bar(0)} 0% — جارٍ التحميل")
            except Exception:  # noqa: BLE001
                pass

        # انتظار انتهاء المعالجة: الحالة من القاعدة (الطابور/التحميل) + النسبة من status.json
        last_text = ""
        prep_start = 0.0  # توقيت بدء التجهيز (للنسبة التقديرية)
        while True:
            with SessionLocal() as db:
                j = db.get(Job, jid)
                state = j.status if j else "failed"
            if state in ("waiting",):
                # في انتظار الـ worker (أجزاء من الثانية) — نعرض التحميل فورًا
                text = f"⬇️ {_progress_bar(0)} 0% — جارٍ التحميل"
            elif state in ("downloading", "preparing"):
                st = read_status(jid) or {}
                if st.get("stage") == "preparing":
                    # ⚙️ تجهيز = دمج ffmpeg — يُقدّر الوقت من حجم الملف وسرعة HDD (~40MB/s)
                    import time
                    now = time.monotonic()
                    if prep_start == 0.0:
                        prep_start = now
                    elapsed = now - prep_start
                    file_size = st.get("size", 0) or st.get("total", 0)
                    if file_size > 0:
                        est_sec = max(3, file_size / (40 * 1024 * 1024))  # تقدير: 40MB/s
                        pct = min(95, int(elapsed / est_sec * 100))
                        text = f"🔧 {_progress_bar(pct)} {pct}% — جارٍ تجهيز المقطع..."
                    else:
                        # حجم غير معروف — عداد زمني متحرك
                        dots = "." * (1 + int(elapsed) % 3)
                        text = f"🔧 جارٍ تجهيز المقطع{dots} ({int(elapsed)}s)"
                else:
                    prep_start = 0.0  # إعادة تعيين
                    pct = st.get("pct", 0)
                    total = st.get("total", 0)
                    size = st.get("size", 0)
                    if pct > 0:
                        text = f"⬇️ {_progress_bar(pct)} {pct}% — جارٍ التحميل"
                    elif total == 0 and size > 0:
                        # حجم كلي مجهول: أظهر MB المحمّلة بدل 0% المتجمد
                        text = f"⬇️ {_fmt_mb(size)} محمّلة — جارٍ التحميل"
                    else:
                        text = f"⬇️ {_progress_bar(pct)} {pct}% — جارٍ التحميل"
            else:
                break
            if text != last_text and bar is not None:
                bar = await _edit_progress(bar, text)
                last_text = text
            await asyncio.sleep(1)

        with SessionLocal() as db:
            j = db.get(Job, jid)
            state = j.status if j else "failed"
            path, size, token, title = (j.file_path, j.file_size, j.download_token, j.title) if j else (None, None, None, None)
            err = j.error if j else None
            is_audio = (j.requested_format == "audio") if j else False

        if state == "ready" and path and size is not None:
            if size > settings.telegram_send_limit_bytes:
                # أكبر من 2GB — لا يمكن إرساله عبر تيليجرام، نعطي رابط التحميل فقط
                # 🧹 لا نحذف الملف هنا! cleanup يتكفل به بعد انتهاء expires_at (24h)
                # (كان يُحذف فورًا = الرابط ميت 404 — إصلاح 2026-08-10)
                size_str = _fmt_mb(size)
                if bar is not None:
                    bar = await _edit_progress(bar, f"✅ تم التحميل ({size_str}) — يتجاوز حد 2GB.\nرابط التحميل: {settings.base_url}/download/{token}")
                else:
                    await bot.send_message(chat_id, f"✅ تم التحميل ({size_str}) — يتجاوز حد 2GB.\nرابط التحميل: {settings.base_url}/download/{token}")
                clear_status(jid)
                return
            # ← مرحلة الرفع: نفس الرسالة تتحول لشريط رفع
            size_str = _fmt_mb(size)
            if bar is not None:
                bar = await _edit_progress(bar, f"✅ تم التحميل ({size_str}) — ⬆️ {_progress_bar(0)} 0% — جارٍ الرفع")
            up_state["pct"] = 0
            caption = (title or "الفيديو")[:1024]

            async def do_send():
                # InputFile(read_file_handle=False): يبقي الملف متدفقًا (بدل قراءته كاملًا في الذاكرة)
                # — ضروري لتتبع التقدم ولتفادي استهلاك 2GB رام للملفات الكبيرة
                from telegram import InputFile
                if is_audio:
                    with open(path, "rb") as f:
                        # الصوت: اسم المقطع في الوصف
                        await bot.send_audio(chat_id, InputFile(f, read_file_handle=False), caption=f"🎵 {caption}")
                else:
                    # 🎬 معاينة + بيانات حقيقية: بدونها يعرض تيليجرام الفيديو كملف (duration=0)
                    # لأن خادم Bot API المحلي لا يعالج الملفات تلقائيًا — نجهّزها بأنفسنا
                    meta = _video_meta(path, Path(path).parent)
                    thumb_path = meta.pop("thumbnail", None)
                    with open(path, "rb") as f:
                        if thumb_path:
                            with open(thumb_path, "rb") as tf:
                                await bot.send_video(
                                    chat_id, InputFile(f, read_file_handle=False),
                                    thumbnail=InputFile(tf),
                                    supports_streaming=True, **meta)
                        else:
                            await bot.send_video(
                                chat_id, InputFile(f, read_file_handle=False),
                                supports_streaming=True, **meta)

            send_task = asyncio.create_task(do_send())
            last_up = -1
            while not send_task.done():
                pct = up_state["pct"]
                if pct != last_up and bar is not None:
                    bar = await _edit_progress(bar, f"⬆️ {_progress_bar(pct)} {pct}% — جارٍ الرفع")
                    last_up = pct
                await asyncio.sleep(1)
            try:
                await send_task
            except Exception as e:  # noqa: BLE001
                log.exception("send failed, falling back to link")
                if bar is not None:
                    bar = await _edit_progress(bar, f"⚠️ تعذر الإرسال المباشر ({str(e)[:100]}).\nرابط التحميل: {settings.base_url}/download/{token}")
                else:
                    await bot.send_message(chat_id, f"⚠️ تعذر الإرسال المباشر ({str(e)[:100]}).\nرابط التحميل: {settings.base_url}/download/{token}")
                # 🧹 لا نحذف الملف هنا! المستخدم سيحمّله من الرابط — cleanup يتكفل
                # بعد انتهاء expires_at. (كان يُحذف = رابط ميت 404 — إصلاح 2026-08-10)
                clear_status(jid)
                return
            # 🧹 قرار المستخدم (2026-08): بعد وصول المقطع يُحذف شريط الحالة — يبقى المقطع وحده فقط
            if bar is not None:
                try:
                    await bar.delete()
                except Exception:  # noqa: BLE001
                    pass
            # 🗑️ حذف فوري مضمون — الملف يُمحى من التخزين لحظة وصوله للمستخدم
            # (باستخدام path المضمون بدل كائن SQLAlchemy المنفصل الذي قد يفشل بصمت)
            _delete_media(path)
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
    finally:
        # 🧹 إغلاق عميل الرفع (AsyncClient + pool الاتصالات) — كان يتسرب لكل وظيفة
        try:
            await bot.shutdown()
        except Exception:  # noqa: BLE001
            pass

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
    app.add_handler(CommandHandler("ip", ip_command))
    app.add_handler(CallbackQueryHandler(on_ip_callback, pattern=r"^ip:"))
    app.add_handler(CallbackQueryHandler(on_pick_callback, pattern=r"^pick:"))
    # ملاحظة: filters.Document كلاس في PTB v21 ويكسر المعالج — استخدم filters.ATTACHMENT (نسخة جاهزة)
    app.add_handler(MessageHandler(filters.ATTACHMENT, receive_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive))
    app.post_init = _set_bot_commands
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




async def _set_bot_commands(app: Application):
    """تسجيل الأوامر الظاهرة فقط: /start و /ip (يحذف القديمة)."""
    commands = [
        {"command": "start", "description": "بدء المحادثة"},
        {"command": "ip", "description": "عرض وتغيير عنوان التحميل (IP)"},
    ]
    try:
        for scope in ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"}):
            await app.bot._post("deleteMyCommands", {"scope": scope})
        await app.bot._post("setMyCommands", {"commands": commands, "scope": {"type": "default"}})
        log.info("bot commands: start, ip")
    except Exception:
        log.exception("set_my_commands failed")


def main():
    app = build_application()
    log.info("bot started, polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
