import os, secrets, re, hashlib, hmac, mimetypes
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse
from .config import settings
from .db import init_db, SessionLocal, Job, utcnow
from .security import validate_public_url, read_token


def _rate_key(request: Request) -> str:
    # عنوان الـ client الحقيقي فقط — slowapi الافتراضي يقرأ X-Forwarded-For
    # (يسمح بتجاوز الحد بتزوير الترويسة)
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_rate_key)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.limiter = limiter
app.mount('/static', StaticFiles(directory='app/static'), name='static')


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "طلبات كثيرة — انتظر قليلاً ثم أعد المحاولة."})


class InspectRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)

class CreateRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    format_id: str = Field(default='best', max_length=8,
                           pattern=r'^(best|audio|\d{2,4})$')


def owner(request):
    return request.client.host if request.client else 'unknown'


def job_json(j):
    from .status import read_status
    st = read_status(j.id)
    return {'id': j.id, 'title': j.title, 'thumbnail': j.thumbnail, 'status': j.status,
            'format_id': j.requested_format, 'file_size': j.file_size, 'duration': j.duration,
            'error': j.error,
            'pct': (st or {}).get('pct', 0) if st else 0,
            'download_url': f'/download/{j.download_token}' if j.download_token else None}


def normalize_formats(raw_formats: list[dict]) -> list[dict]:
    """قائمة جودات موحدة: الأفضل + كل دقة حقيقية من المقطع + صوت فقط — بلا جودات وهمية.

    2026-08: دقة واحدة لكل ارتفاع (تفضيل mp4) — كان يعطي زرين بنفس format_id.
    """
    unique: dict[int, dict] = {}
    for item in raw_formats or []:
        label = str(item.get("label") or "")
        # يقبل: "1080p / mp4" و "1080p60" و "2160p" — بلا صيغ وهمية
        m = re.match(r"\s*(\d+)\s*p(?:60)?(?:\s*/\s*([a-z0-9]+))?", label, re.IGNORECASE)
        if not m:
            continue
        height, ext = int(m.group(1)), (m.group(2) or "mp4").lower()
        size = item.get("size")
        score = 0 if ext == "mp4" else 1  # mp4 أعلى أولوية (توافق أوسع)
        cur = unique.get(height)
        if cur is None or score < cur["_score"] or (
                score == cur["_score"] and (size or 0) > (cur.get("size") or 0)):
            unique[height] = {"id": str(height), "label": f"{height}p / {ext}",
                              "size": size, "_score": score}
    qualities = []
    for h in sorted(unique, reverse=True):
        entry = dict(unique[h])
        entry.pop("_score", None)
        qualities.append(entry)
    # ⚠️ إن لم توجد جودات حقيقية: نعرض "الأفضل" و"صوت فقط" فقط — ممنوع اختلاق دقات
    return [{"id": "best", "label": "⭐ الأفضل تلقائيًا", "size": None},
            *qualities,
            {"id": "audio", "label": "🎵 صوت فقط", "size": None}]


@app.get('/', response_class=HTMLResponse)
def home(): return Path('app/static/index.html').read_text(encoding='utf-8')


@app.post('/api/inspect')
@limiter.limit("10/minute")
def inspect(data: InspectRequest, request: Request):
    from .extractor import UniversalExtractor
    url = validate_public_url(data.url)
    ex = UniversalExtractor(
        request_timeout=settings.request_timeout_seconds,
        max_filesize=settings.max_file_size_bytes,
    )
    try:
        d = ex.inspect(url)
    except Exception as e:
        raise HTTPException(400, f'تعذر قراءة الفيديو: {str(e)[:180]}')
    finally:
        ex.cleanup()
    return {'title': d.get('title'), 'thumbnail': d.get('thumbnail'),
            'duration': d.get('duration'), 'source': d.get('source'),
            'formats': normalize_formats(d.get('formats', []))}


@app.post('/api/jobs')
@limiter.limit("10/minute")
def create_job(data: CreateRequest, request: Request):
    url = validate_public_url(data.url)
    key = owner(request)
    with SessionLocal() as db:
        active = db.query(Job).filter(
            Job.owner_key == key,
            Job.status.in_(['waiting', 'downloading', 'preparing'])).count()
        if active >= settings.max_active_jobs_per_ip:
            raise HTTPException(429, 'لديك عمليات نشطة كثيرة')
        existing = db.query(Job).filter(
            Job.owner_key == key, Job.url == url, Job.requested_format == data.format_id,
            Job.status.in_(['waiting', 'downloading', 'preparing'])).first()
        if existing:
            return job_json(existing)
        j = Job(id=secrets.token_urlsafe(16), url=url,
                requested_format=data.format_id, status='waiting', owner_key=key)
        db.add(j)
        db.commit()
        db.refresh(j)
        jid = j.id
    if settings.vercel_mode:
        # على Vercel: تنفيذ فوري داخل الطلب (لا Redis ولا عمليات خلفية)
        try:
            process_inline(jid)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger("web").exception("inline processing failed for %s", jid)
        with SessionLocal() as db:
            return job_json(db.get(Job, jid))
    try:
        import redis
        redis.Redis.from_url(settings.redis_url).rpush('yjdownload:jobs', jid)
    except Exception:
        import logging
        logging.getLogger("web").exception("redis push failed for %s", jid)
        import threading
        threading.Thread(target=process_inline, args=(jid,), daemon=True).start()
    return job_json(j)


def process_inline(job_id):
    from .worker import process_job
    process_job(job_id)


@app.get('/api/jobs/{job_id}')
def get_status(job_id: str):
    with SessionLocal() as db:
        j = db.get(Job, job_id)
        if not j:
            raise HTTPException(404, 'الطلب غير موجود')
        return job_json(j)


@app.get('/download/{token}')
def download(token: str):
    try:
        jid = read_token(token)
    except Exception:
        raise HTTPException(404, 'الرابط منتهي أو غير صالح')
    with SessionLocal() as db:
        j = db.get(Job, jid)
        if not j or j.status != 'ready' or not j.file_path or not os.path.isfile(j.file_path):
            raise HTTPException(404, 'الملف غير متاح')
        # 🎬 نوع المحتوى والامتداد الحقيقيان (كان كل شيء يُرسل video/mp4 حتى للصوت)
        suffix = Path(j.file_path).suffix or ".mp4"
        media = mimetypes.guess_type(j.file_path)[0] or "application/octet-stream"
        # مدّد الصلاحية عند كل تنزيل — يمنع cleanup من حذف الملف أثناء تدفقه
        if j.expires_at:
            j.expires_at = utcnow() + timedelta(hours=1)
            db.commit()
        return FileResponse(j.file_path, filename=f'{j.title or jid}{suffix}', media_type=media)


@app.post('/webhook/telegram')
async def telegram_webhook(request: Request):
    """نقطة Webhook تيليجرام (وضع Vercel — لا يوجد polling دائم هناك)."""
    from telegram import Update
    from .bot import handle_update_webhook, webhook_bot
    # سر التوقيع: أي طلب بدون السر الصحيح يُرفض (403)
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    expected = hashlib.sha256(
        f"yjdownload-local-webhook-v1\0{settings.telegram_bot_token}".encode("utf-8")
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(403, "Invalid webhook secret")
    try:
        data = await request.json()
        update = Update.de_json(data, await webhook_bot())
        if update is not None:
            await handle_update_webhook(update)
    except Exception as e:  # noqa: BLE001
        from .bot import log
        log.exception("webhook failed")
        return {'ok': False, 'error': str(e)[:200]}
    return {'ok': True}


@app.get('/api/telegram/status')
async def telegram_status(request: Request, repair: bool = False, drop_pending: bool = False):
    """فحص وإصلاح الويب هوك (للوضع غير المحلي) — محروس بمفتاح إداري إن ضُبط."""
    if settings.admin_secret and request.headers.get("x-admin-secret", "") != settings.admin_secret:
        raise HTTPException(403, "forbidden")
    from telegram import Bot
    bot = Bot(token=settings.telegram_bot_token)
    try:
        if repair:
            await bot.set_webhook(
                url=f"{settings.base_url.rstrip('/')}/webhook/telegram",
                secret_token=hashlib.sha256(
                    f"yjdownload-local-webhook-v1\0{settings.telegram_bot_token}".encode("utf-8")
                ).hexdigest(),
                allowed_updates=["message", "edited_message", "callback_query"],
                drop_pending_updates=bool(drop_pending),
            )
        info = await bot.get_webhook_info()
        return {
            "configured": True,
            "url": info.url,
            "pending_update_count": info.pending_update_count,
            "last_error": str(info.last_error_message or "")[:200],
            "repaired": repair,
        }
    except Exception as e:  # noqa: BLE001
        return {"configured": False, "error": str(e)[:200]}


@app.get('/api/health')
def health():
    return {'status': 'ok', 'vercel': settings.vercel_mode}
