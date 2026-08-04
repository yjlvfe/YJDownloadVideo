from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ffmpeg_location, javascript_runtime_options, settings
from .db import Job, SessionLocal, init_db
from .durable_runtime import (
    RecoverableSelectionStore,
    get_job_queue,
    install_durable_flow_patches,
    start_durable_worker,
)
from .media_choice import normalize_qualities, selector_for_quality
from .platform_runtime import get_youtube_circuit, install_runtime_patches
from .security import read_token, validate_public_url
from .storage_lifecycle import cleanup_expired_storage, storage_stats
from .telegram_api import TelegramAPI, TelegramAPIError

install_runtime_patches()

from .telegram_flow import get_telegram_flow  # noqa: E402

for logger_name in ("httpx", "httpcore"):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

log = logging.getLogger("telegram-webhook")
app = FastAPI(title=settings.app_name)

_cors_origins = {
    value.strip().rstrip("/")
    for value in str(settings.cors_origins or "").split(",")
    if value.strip()
}
_cors_origins.add("https://yj-download-video.vercel.app")
if settings.base_url.startswith(("https://", "http://")):
    _cors_origins.add(settings.base_url.rstrip("/"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(_cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-admin-token"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class InspectRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class CreateRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    format_id: str = Field(default="best", min_length=3, max_length=32)


class JobStatusRequest(BaseModel):
    job_id: str = Field(min_length=8, max_length=128)


class TelegramRepairRequest(BaseModel):
    drop_pending: bool = False


def owner(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _validated_quality(value: str) -> str:
    normalized = str(value or "").strip().lower()
    try:
        selector_for_quality(normalized)
    except ValueError as exc:
        raise HTTPException(400, "الجودة المطلوبة غير مدعومة") from exc
    return normalized


def job_json(job: Job | None) -> dict[str, Any]:
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
        "error": str(job.error or "")[:300] or None,
        "download_url": f"/download/{job.download_token}" if job.download_token else None,
    }


def normalize_formats(raw_formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    options = normalize_qualities(raw_formats)
    result: list[dict[str, Any]] = [
        {"id": "best", "label": "⭐ الأفضل", "size": None}
    ]
    seen = {"best"}
    for option in reversed(options):
        if option.key in seen or option.key == "original":
            continue
        seen.add(option.key)
        result.append({"id": option.key, "label": option.label, "size": option.size})
    if len(result) == 1 and options and options[0].key == "original":
        result[0]["label"] = "الجودة الأصلية"
    return result


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


def _rewrite_remote_download(result: dict[str, Any]) -> dict[str, Any]:
    result = dict(result)
    path = result.get("download_url")
    if isinstance(path, str) and path.startswith("/"):
        result["download_url"] = f"{settings.remote_worker_url.rstrip('/')}{path}"
    return result


async def _remote_post(
    path: str,
    payload: dict[str, Any],
    *,
    context: str,
    timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    from .remote_worker import _post_signed

    return await _post_signed(
        path,
        payload,
        context=context,
        timeout_seconds=timeout_seconds,
    )


def _inspect_local(url: str) -> dict[str, Any]:
    from .extractor import UniversalExtractor

    extractor = UniversalExtractor(
        use_browser=settings.enable_browser_capture and not settings.vercel_mode,
        browser_bin=settings.browser_bin,
        request_timeout=settings.request_timeout_seconds,
        max_filesize=settings.max_file_size_bytes,
    )
    try:
        result = extractor.inspect(url)
    finally:
        extractor.cleanup()
    return {
        "title": result.get("title"),
        "thumbnail": result.get("thumbnail"),
        "duration": result.get("duration"),
        "source": result.get("source"),
        "formats": normalize_formats(result.get("formats", [])),
    }


def _create_job_local(data: CreateRequest, owner_key: str) -> dict[str, Any]:
    url = validate_public_url(data.url)
    quality = _validated_quality(data.format_id)
    with SessionLocal() as db:
        active = db.query(Job).filter(
            Job.owner_key == owner_key,
            Job.status.in_(["waiting", "downloading", "preparing"]),
        ).count()
        if active >= settings.max_active_jobs_per_ip:
            raise HTTPException(429, "لديك عمليات نشطة كثيرة")
        job = Job(
            id=secrets.token_urlsafe(16),
            url=url,
            requested_format=quality,
            status="waiting",
            owner_key=owner_key,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        result = job_json(job)
    get_job_queue().enqueue(
        idempotency_key=job.id,
        kind="web_download",
        payload={"job_id": job.id},
    )
    return result


def _get_job_local(job_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "الطلب غير موجود")
        return job_json(job)


def _worker_secret() -> str:
    return settings.worker_shared_secret or settings.telegram_bot_token


def _verify_signed_worker_request(raw: bytes, request: Request, context: str) -> bool:
    from .remote_worker import verify_worker_request, verify_worker_signature

    signature = request.headers.get("x-telegram-worker-auth", "")
    timestamp = request.headers.get("x-worker-timestamp", "")
    nonce = request.headers.get("x-worker-nonce", "")
    if timestamp and nonce:
        queue = get_job_queue()
        return verify_worker_request(
            _worker_secret(),
            raw,
            signature,
            context,
            timestamp=timestamp,
            nonce=nonce,
            consume_nonce=lambda value, expires: queue.consume_nonce(
                value,
                expires_at=expires,
            ),
        )
    # Temporary compatibility while the old Vercel deployment is promoted.
    return verify_worker_signature(
        settings.telegram_bot_token,
        raw,
        signature,
        context,
    )


def _parse_json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Invalid {label}") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, f"Invalid {label}")
    return value


def _require_admin(token: str | None) -> None:
    if not settings.admin_api_token:
        raise HTTPException(503, "Admin API is not configured")
    if not token or not hmac.compare_digest(token, settings.admin_api_token):
        raise HTTPException(401, "Admin authentication required")


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(max(60, int(settings.cleanup_interval_seconds)))
        await asyncio.to_thread(
            cleanup_expired_storage,
            settings.storage_path,
            now=time.time(),
            ttl_seconds=settings.file_ttl_seconds,
            active_paths=set(),
        )


@app.on_event("startup")
async def startup() -> None:
    init_db()
    app.state.background_tasks = []
    if settings.vercel_mode:
        return
    queue = get_job_queue()
    install_durable_flow_patches(queue)
    await asyncio.to_thread(
        cleanup_expired_storage,
        settings.storage_path,
        now=time.time(),
        ttl_seconds=settings.file_ttl_seconds,
        active_paths=set(),
    )
    worker_task = start_durable_worker()
    if worker_task is not None:
        app.state.background_tasks.append(worker_task)
    app.state.background_tasks.append(asyncio.create_task(_cleanup_loop()))


@app.on_event("shutdown")
async def shutdown() -> None:
    for task in getattr(app.state, "background_tasks", []):
        task.cancel()
    for task in getattr(app.state, "background_tasks", []):
        try:
            await task
        except asyncio.CancelledError:
            pass


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return Path("app/static/index.html").read_text(encoding="utf-8")


@app.post("/api/inspect")
async def inspect(data: InspectRequest) -> dict[str, Any]:
    if settings.vercel_mode:
        try:
            return await _remote_post(
                "/media/inspect",
                data.model_dump(),
                context="media-inspect",
                timeout_seconds=60.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, str(exc)[:180]) from exc
    try:
        return await asyncio.to_thread(_inspect_local, validate_public_url(data.url))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"تعذر قراءة الفيديو: {str(exc)[:180]}") from exc


@app.post("/api/jobs")
async def create_job(data: CreateRequest, request: Request) -> dict[str, Any]:
    if settings.vercel_mode:
        try:
            result = await _remote_post(
                "/media/jobs",
                {**data.model_dump(), "owner_key": owner(request)},
                context="media-create",
            )
            return _rewrite_remote_download(result)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, str(exc)[:180]) from exc
    return await asyncio.to_thread(_create_job_local, data, owner(request))


@app.get("/api/jobs/{job_id}")
async def get_status(job_id: str) -> dict[str, Any]:
    if settings.vercel_mode:
        try:
            result = await _remote_post(
                "/media/job-status",
                {"job_id": job_id},
                context="media-status",
                timeout_seconds=30.0,
            )
            return _rewrite_remote_download(result)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, str(exc)[:180]) from exc
    return await asyncio.to_thread(_get_job_local, job_id)


@app.get("/download/{token}")
def download(token: str):
    if settings.vercel_mode:
        return RedirectResponse(
            f"{settings.remote_worker_url.rstrip('/')}/download/{token}",
            status_code=307,
        )
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


async def _telegram_status_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if settings.vercel_mode:
        from .remote_worker import telegram_call

        return await telegram_call(method, payload)
    return await _telegram_api().call(method, payload)


@app.get("/api/telegram/status")
async def telegram_status(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_admin(x_admin_token)
    if not settings.telegram_bot_token:
        return {"configured": False, "connected": False}
    try:
        info = await _telegram_status_call("getWebhookInfo", {})
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "connected": False,
            "error": str(exc)[:160],
        }
    current_url = str(info.get("url") or "")
    return {
        "configured": True,
        "connected": current_url.rstrip("/") == telegram_webhook_url().rstrip("/"),
        "pending_updates": int(info.get("pending_update_count") or 0),
        "last_error": str(info.get("last_error_message") or "")[:160],
    }


@app.post("/admin/telegram/webhook/repair")
async def repair_telegram_webhook(
    data: TelegramRepairRequest,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(x_admin_token)
    await _telegram_status_call(
        "setWebhook",
        {
            "url": telegram_webhook_url(),
            "secret_token": telegram_webhook_secret(),
            "allowed_updates": ["message", "edited_message", "callback_query"],
            "drop_pending_updates": bool(data.drop_pending),
            "max_connections": 10,
        },
    )
    return {"ok": True, "dropped_pending": bool(data.drop_pending)}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> dict[str, Any]:
    if not settings.telegram_bot_token:
        raise HTTPException(503, "Telegram bot is not configured")
    supplied_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(supplied_secret, telegram_webhook_secret()):
        raise HTTPException(403, "Invalid Telegram webhook secret")
    try:
        data = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "Invalid Telegram update") from exc
    if not isinstance(data, dict) or not isinstance(data.get("update_id"), int):
        raise HTTPException(400, "Invalid Telegram update")

    if settings.vercel_mode:
        from .remote_worker import RemoteWorkerError, forward_telegram_update

        try:
            await forward_telegram_update(data)
        except RemoteWorkerError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True, "delivery": "render-2gb"}

    _, created = await asyncio.to_thread(
        get_job_queue().accept_update,
        int(data["update_id"]),
        data,
    )
    return {"ok": True, "delivery": "local", "created": created}


@app.post("/telegram/update", status_code=202)
async def worker_telegram_update(request: Request) -> dict[str, Any]:
    if settings.vercel_mode:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    if not _verify_signed_worker_request(raw, request, "telegram-update"):
        raise HTTPException(401, "Invalid worker signature")
    data = _parse_json_object(raw, "Telegram update")
    update_id = data.get("update_id")
    if not isinstance(update_id, int):
        raise HTTPException(400, "Invalid Telegram update")
    job, created = await asyncio.to_thread(
        get_job_queue().accept_update,
        update_id,
        data,
    )
    return {"ok": True, "accepted": True, "created": created, "job_id": job.id}


@app.post("/telegram/call/{method}")
async def worker_telegram_call(method: str, request: Request):
    if settings.vercel_mode:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    from .remote_worker import ALLOWED_TELEGRAM_METHODS

    if method not in ALLOWED_TELEGRAM_METHODS:
        raise HTTPException(404, "Unsupported Telegram method")
    if not _verify_signed_worker_request(raw, request, f"telegram-call:{method}"):
        raise HTTPException(401, "Invalid worker signature")
    payload = _parse_json_object(raw, "Telegram payload")
    try:
        result = await _telegram_api().call(method, payload)
    except TelegramAPIError as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "description": str(exc)[:200]},
        )
    return {"ok": True, "result": result}


@app.post("/media/inspect")
async def worker_media_inspect(request: Request) -> dict[str, Any]:
    if settings.vercel_mode:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    if not _verify_signed_worker_request(raw, request, "media-inspect"):
        raise HTTPException(401, "Invalid worker signature")
    payload = InspectRequest.model_validate(_parse_json_object(raw, "inspect payload"))
    try:
        return await asyncio.to_thread(_inspect_local, validate_public_url(payload.url))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)[:180]) from exc


@app.post("/media/jobs")
async def worker_media_create(request: Request) -> dict[str, Any]:
    if settings.vercel_mode:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    if not _verify_signed_worker_request(raw, request, "media-create"):
        raise HTTPException(401, "Invalid worker signature")
    payload = _parse_json_object(raw, "media create payload")
    data = CreateRequest.model_validate(payload)
    return await asyncio.to_thread(
        _create_job_local,
        data,
        str(payload.get("owner_key") or "vercel"),
    )


@app.post("/media/job-status")
async def worker_media_status(request: Request) -> dict[str, Any]:
    if settings.vercel_mode:
        raise HTTPException(404, "Not found")
    raw = await request.body()
    if not _verify_signed_worker_request(raw, request, "media-status"):
        raise HTTPException(401, "Invalid worker signature")
    payload = JobStatusRequest.model_validate(_parse_json_object(raw, "job status payload"))
    return await asyncio.to_thread(_get_job_local, payload.job_id)


def _cookie_configured(platform: str) -> bool:
    names = {
        "youtube": ("YOUTUBE_COOKIES_FILE", "YOUTUBE_COOKIES_B64", "youtube.txt"),
        "instagram": ("INSTAGRAM_COOKIES_FILE", "INSTAGRAM_COOKIES_B64", "instagram.txt"),
    }[platform]
    explicit, encoded, filename = names
    explicit_path = os.getenv(explicit, "").strip()
    return bool(
        (explicit_path and Path(explicit_path).is_file())
        or os.getenv(encoded, "").strip()
        or (settings.cookies_path / filename).is_file()
    )


def _yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001
        return "unknown"


@app.get("/health")
@app.get("/api/health")
async def health() -> dict[str, Any]:
    if settings.vercel_mode:
        from .remote_worker import remote_worker_health

        remote = await remote_worker_health()
        return {
            **remote,
            "vercel": True,
            "ingressGitSha": settings.git_sha,
            "remoteWorkerConfigured": bool(settings.remote_worker_url),
        }

    local_ready = False
    selection_count = 0
    if settings.telegram_bot_token:
        try:
            flow = get_telegram_flow()
            selection_count = flow.selection_count
            local_ready = await flow.api.ready()
        except Exception:  # noqa: BLE001
            local_ready = False

    queue = get_job_queue()
    retained = storage_stats(settings.storage_path)
    ffmpeg_path = Path(ffmpeg_location())
    circuit = get_youtube_circuit().snapshot()
    js_ready = bool(javascript_runtime_options())
    return {
        "ok": bool(local_ready and ffmpeg_path.is_file() and js_ready),
        "vercel": False,
        "gitSha": settings.git_sha,
        "ytDlpVersion": _yt_dlp_version(),
        "remoteWorkerConfigured": bool(settings.remote_worker_url),
        "localApiReady": local_ready,
        "queueDepth": queue.queue_depth(),
        "activeJobs": queue.active_count(),
        "selectionCount": selection_count,
        "maxFileBytes": settings.telegram_send_limit_bytes,
        "retainedMediaCount": retained.file_count,
        "retainedMediaBytes": retained.total_bytes,
        "storageFreeBytes": retained.free_bytes,
        "ffmpegReady": ffmpeg_path.is_file(),
        "javascriptRuntimeReady": js_ready,
        "youtubeCircuitState": circuit.state,
        "youtubeCircuitFailures": circuit.failure_count,
        "youtubeCookiesConfigured": _cookie_configured("youtube"),
        "instagramCookiesConfigured": _cookie_configured("instagram"),
        "youtubeProxyConfigured": bool(settings.youtube_proxy_url),
        "publicYoutubeFallbackEnabled": bool(settings.enable_public_youtube_fallback),
        "workerAuthMode": "dedicated" if settings.worker_shared_secret else "legacy",
    }
