import hashlib
import hmac
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .db import Job, SessionLocal, init_db
from .security import read_token, validate_public_url

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class InspectRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class CreateRequest(BaseModel):
    url: str
    format_id: str = "best"


def owner(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def job_json(job: Job | None) -> dict:
    if job is None:
        return {"status": "failed", "error": "تعذر قراءة حالة العملية"}
    return {
        "id": job.id,
        "title": job.title,
        "thumbnail": job.thumbnail,
        "status": job.status,
        "format_id": job.requested_format,
        "file_size": job.file_size,
        "duration": job.duration,
        "error": job.error,
        "download_url": f"/download/{job.download_token}" if job.download_token else None,
    }


def telegram_webhook_url() -> str:
    return f"{settings.base_url.rstrip('/')}/webhook/telegram"


def telegram_webhook_secret() -> str:
    material = f"{settings.secret_key}:{settings.telegram_bot_token}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return Path("app/static/index.html").read_text(encoding="utf-8")


@app.post("/api/inspect")
def inspect(data: InspectRequest) -> dict:
    from .extractor import UniversalExtractor

    url = validate_public_url(data.url)
    extractor = UniversalExtractor(
        use_browser=settings.enable_browser_capture and not settings.vercel_mode,
        browser_bin=settings.browser_bin,
        request_timeout=settings.request_timeout_seconds,
        max_filesize=settings.max_file_size_bytes,
    )
    try:
        result = extractor.inspect(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"تعذر قراءة الفيديو: {str(exc)[:180]}") from exc
    finally:
        extractor.cleanup()

    return {
        "title": result.get("title"),
        "thumbnail": result.get("thumbnail"),
        "duration": result.get("duration"),
        "source": result.get("source"),
        "formats": result.get("formats", []),
    }


@app.post("/api/jobs")
def create_job(data: CreateRequest, request: Request) -> dict:
    url = validate_public_url(data.url)
    key = owner(request)

    with SessionLocal() as db:
        active = (
            db.query(Job)
            .filter(
                Job.owner_key == key,
                Job.status.in_(["waiting", "downloading", "preparing"]),
            )
            .count()
        )
        if active >= settings.max_active_jobs_per_ip:
            raise HTTPException(429, "لديك عمليات نشطة كثيرة")

        existing = (
            db.query(Job)
            .filter(
                Job.owner_key == key,
                Job.url == url,
                Job.status.in_(["waiting", "downloading", "preparing"]),
            )
            .first()
        )
        if existing:
            return job_json(existing)

        job = Job(
            id=secrets.token_urlsafe(16),
            url=url,
            requested_format=data.format_id,
            status="waiting",
            owner_key=key,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    if settings.vercel_mode:
        # Vercel has no persistent worker process, so this request performs the job.
        try:
            process_inline(job_id)
        except Exception:  # noqa: BLE001
            pass
        with SessionLocal() as db:
            return job_json(db.get(Job, job_id))

    try:
        import redis

        redis.Redis.from_url(settings.redis_url).rpush("yjdownload:jobs", job_id)
    except Exception:  # noqa: BLE001
        import threading

        threading.Thread(target=process_inline, args=(job_id,), daemon=True).start()
    return job_json(job)


def process_inline(job_id: str) -> None:
    from .worker import process_job

    process_job(job_id)


@app.get("/api/jobs/{job_id}")
def get_status(job_id: str) -> dict:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "الطلب غير موجود")
        return job_json(job)


@app.get("/download/{token}")
def download(token: str):
    try:
        job_id = read_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, "الرابط منتهي أو غير صالح") from exc

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if (
            not job
            or job.status != "ready"
            or not job.file_path
            or not os.path.isfile(job.file_path)
        ):
            raise HTTPException(404, "الملف غير متاح")
        return FileResponse(
            job.file_path,
            filename=f"{job.title or job_id}.mp4",
            media_type="video/mp4",
        )


@app.get("/api/telegram/status")
async def telegram_status(repair: bool = False) -> dict:
    """Return safe webhook diagnostics and optionally repair the Telegram webhook."""
    if not settings.telegram_bot_token:
        return {
            "configured": False,
            "connected": False,
            "repaired": False,
            "reason": "TELEGRAM_BOT_TOKEN is missing in Vercel environment variables",
            "expected_url": telegram_webhook_url(),
        }

    from telegram import Bot, Update

    expected_url = telegram_webhook_url()
    try:
        async with Bot(settings.telegram_bot_token) as bot:
            repaired = False
            if repair:
                repaired = bool(
                    await bot.set_webhook(
                        url=expected_url,
                        secret_token=telegram_webhook_secret(),
                        allowed_updates=Update.ALL_TYPES,
                        drop_pending_updates=False,
                    )
                )
            info = await bot.get_webhook_info()
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "connected": False,
            "repaired": False,
            "expected_url": expected_url,
            "error": str(exc)[:200],
        }

    return {
        "configured": True,
        "connected": info.url.rstrip("/") == expected_url.rstrip("/"),
        "repaired": repaired,
        "expected_url": expected_url,
        "current_url": info.url,
        "pending_updates": info.pending_update_count,
        "last_error": (info.last_error_message or "")[:200],
    }


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict:
    """Receive Telegram updates through the Vercel webhook."""
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot is not configured")

    supplied_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(supplied_secret, telegram_webhook_secret()):
        raise HTTPException(403, "Invalid Telegram webhook secret")

    from telegram import Update
    from .bot import handle_update_webhook, webhook_bot

    try:
        data = await request.json()
        update = Update.de_json(data, await webhook_bot())
        if update is not None:
            await handle_update_webhook(update)
    except Exception as exc:  # noqa: BLE001
        from .bot import log

        log.exception("webhook failed")
        raise HTTPException(500, "Telegram update processing failed") from exc
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "vercel": settings.vercel_mode,
        "telegram_configured": bool(settings.telegram_bot_token),
    }
