from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .db import Job, SessionLocal, init_db
from .security import read_token, validate_public_url
from .telegram_api import TelegramAPI
from .telegram_flow import get_telegram_flow

for logger_name in ("httpx", "httpcore"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

log = logging.getLogger("telegram-webhook")
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


def normalize_formats(raw_formats: list[dict]) -> list[dict]:
    unique: dict[int, dict] = {}
    for item in raw_formats or []:
        label = str(item.get("label") or "")
        match = re.search(r"(?<!\d)(\d{2,4})p(?!\d)", label, re.IGNORECASE)
        height = item.get("height") or (int(match.group(1)) if match else None)
        if not height:
            continue
        size = item.get("filesize") or item.get("filesize_approx") or item.get("size")
        candidate = {
            "id": f"bestvideo[height<={height}]+bestaudio/best[height<={height}]",
            "label": f"{height}p",
            "size": size,
        }
        previous = unique.get(int(height))
        if previous is None or (size or 0) > (previous.get("size") or 0):
            unique[int(height)] = candidate
    if not unique:
        return [{"id": "bestvideo*+bestaudio/best", "label": "الجودة الأصلية", "size": None}]
    return [unique[key] for key in sorted(unique)]


def telegram_webhook_url() -> str:
    configured = str(settings.telegram_webhook_url or "").strip()
    if configured:
        return configured
    return f"{settings.base_url.rstrip('/')}/webhook/telegram"


def telegram_webhook_secret() -> str:
    material = f"yjdownload-local-webhook-v1\0{settings.telegram_bot_token}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _telegram_api() -> TelegramAPI:
    return TelegramAPI(
        token=settings.telegram_bot_token,
        base_url=settings.telegram_bot_api_base or "https://api.telegram.org",
    )


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
        "formats": normalize_formats(result.get("formats", [])),
    }


@app.post("/api/jobs")
def create_job(data: CreateRequest, request: Request) -> dict:
    url = validate_public_url(data.url)
    key = owner(request)
    with SessionLocal() as db:
        active = db.query(Job).filter(
            Job.owner_key == key,
            Job.status.in_(["waiting", "downloading", "preparing"]),
        ).count()
        if active >= settings.max_active_jobs_per_ip:
            raise HTTPException(429, "لديك عمليات نشطة كثيرة")
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
        process_inline(job_id)
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
        if not job or job.status != "ready" or not job.file_path or not os.path.isfile(job.file_path):
            raise HTTPException(404, "الملف غير متاح")
        return FileResponse(
            job.file_path,
            filename=f"{job.title or job_id}.mp4",
            media_type="video/mp4",
        )


@app.get("/api/telegram/status")
async def telegram_status(repair: bool = False, drop_pending: bool = False) -> dict:
    if not settings.telegram_bot_token:
        return {"configured": False, "connected": False}
    api = _telegram_api()
    expected_url = telegram_webhook_url()
    repaired = False
    try:
        if repair:
            await api.call(
                "setWebhook",
                {
                    "url": expected_url,
                    "secret_token": telegram_webhook_secret(),
                    "allowed_updates": ["message", "edited_message", "callback_query"],
                    "drop_pending_updates": bool(drop_pending),
                    "max_connections": 10,
                },
            )
            repaired = True
        info = await api.call("getWebhookInfo", {})
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "connected": False,
            "repaired": False,
            "expected_url": expected_url,
            "error": str(exc)[:200],
        }
    current_url = str(info.get("url") or "")
    return {
        "configured": True,
        "connected": current_url.rstrip("/") == expected_url.rstrip("/"),
        "repaired": repaired,
        "expected_url": expected_url,
        "current_url": current_url,
        "pending_updates": int(info.get("pending_update_count") or 0),
        "last_error": str(info.get("last_error_message") or "")[:200],
    }


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict:
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot is not configured")
    supplied_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(supplied_secret, telegram_webhook_secret()):
        raise HTTPException(403, "Invalid Telegram webhook secret")
    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Invalid Telegram update") from exc
    asyncio.create_task(get_telegram_flow().handle_update(data))
    return {"ok": True}


def _storage_stats() -> tuple[int, int]:
    count = 0
    size = 0
    for path in settings.storage_path.rglob("*"):
        if path.is_file():
            count += 1
            try:
                size += path.stat().st_size
            except OSError:
                pass
    return count, size


@app.get("/health")
@app.get("/api/health")
async def health() -> dict:
    local_ready = False
    queue_depth = 0
    selection_count = 0
    if settings.telegram_bot_token:
        try:
            flow = get_telegram_flow()
            queue_depth = flow.queue_depth
            selection_count = flow.selection_count
            await flow.api.call("getMe", {})
            local_ready = True
        except Exception:  # noqa: BLE001
            local_ready = False
    retained_count, retained_bytes = _storage_stats()
    return {
        "ok": local_ready,
        "localApiReady": local_ready,
        "queueDepth": queue_depth,
        "selectionCount": selection_count,
        "maxFileBytes": settings.telegram_send_limit_bytes,
        "retainedMediaCount": retained_count,
        "retainedMediaBytes": retained_bytes,
    }
