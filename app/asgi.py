from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from . import main as main_module
from . import pot_runtime_patch as _pot_runtime_patch  # noqa: F401
from . import network_runtime_patch as _network_runtime_patch  # noqa: F401
from .config import settings
from .db import Job, SessionLocal
from .durable_runtime import get_job_queue
from .inspect_runtime import inspect_with_timeout
from .main import app as fastapi_app
from .media_choice import safe_media_title
from .rate_limit import SlidingWindowLimiter
from .redaction import sanitize_external_error
from .security import read_token
from .telegram_flow import TelegramFlow


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]


_original_safe_error = TelegramFlow._safe_error
_EXPLICIT_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}


def _sanitized_safe_error(exc: Exception) -> str:
    return sanitize_external_error(
        _original_safe_error(exc),
        secrets_to_hide=(
            settings.telegram_bot_token,
            settings.telegram_api_hash,
            settings.worker_shared_secret,
            settings.admin_api_token,
            settings.youtube_proxy_url,
        ),
        limit=220,
    )


TelegramFlow._safe_error = staticmethod(_sanitized_safe_error)


def _verify_transition_worker_request(raw: bytes, request: Request, context: str) -> bool:
    from .remote_worker import verify_worker_request, verify_worker_signature

    signature_v2 = request.headers.get("x-worker-signature-v2", "")
    timestamp = request.headers.get("x-worker-timestamp", "")
    nonce = request.headers.get("x-worker-nonce", "")
    if signature_v2 and timestamp and nonce:
        queue = get_job_queue()
        secret = settings.worker_shared_secret or settings.telegram_bot_token
        if verify_worker_request(
            secret,
            raw,
            signature_v2,
            context,
            timestamp=timestamp,
            nonce=nonce,
            consume_nonce=lambda value, expires: queue.consume_nonce(
                value,
                expires_at=expires,
            ),
        ):
            return True

    return verify_worker_signature(
        settings.telegram_bot_token,
        raw,
        request.headers.get("x-telegram-worker-auth", ""),
        context,
    )


main_module._verify_signed_worker_request = _verify_transition_worker_request


def _bounded_inspect_local(url: str) -> dict[str, Any]:
    timeout = min(40, max(15, int(settings.request_timeout_seconds)))
    result = inspect_with_timeout(url, timeout_seconds=timeout)
    return {
        "title": result.get("title"),
        "thumbnail": result.get("thumbnail"),
        "duration": result.get("duration"),
        "source": result.get("source"),
        "formats": main_module.normalize_formats(result.get("formats", [])),
    }


# FastAPI route handlers resolve this module global at call time. Install the
# bounded version before importing the temporary acceptance probe so every
# inspection path has the same process isolation and timeout.
main_module._inspect_local = _bounded_inspect_local
from . import live_acceptance_probe as _live_acceptance_probe  # noqa: F401,E402


def media_response_metadata(path: str | Path, title: str) -> tuple[str, str]:
    media_path = Path(path)
    suffix = media_path.suffix.lower() or ".bin"
    filename = safe_media_title(title or media_path.stem or "media", suffix)
    media_type = (
        _EXPLICIT_MEDIA_TYPES.get(suffix)
        or mimetypes.guess_type(media_path.name)[0]
        or "application/octet-stream"
    )
    return filename, media_type


def _install_download_route() -> None:
    fastapi_app.router.routes = [
        route
        for route in fastapi_app.router.routes
        if getattr(route, "path", None) != "/download/{token}"
    ]

    def stream_download(token: str):
        if settings.vercel_mode:
            return RedirectResponse(
                f"{settings.remote_worker_url.rstrip('/')}/download/{token}",
                status_code=307,
            )
        try:
            job_id = read_token(token)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(404, "الرابط منتهي أو غير صالح") from exc

        with SessionLocal() as database:
            job = database.get(Job, job_id)
            if (
                not job
                or job.status != "ready"
                or not job.file_path
                or not os.path.isfile(job.file_path)
            ):
                raise HTTPException(404, "الملف غير متاح")
            filename, media_type = media_response_metadata(
                job.file_path,
                str(job.title or job_id),
            )
            return FileResponse(
                job.file_path,
                filename=filename,
                media_type=media_type,
            )

    fastapi_app.add_api_route(
        "/download/{token}",
        stream_download,
        methods=["GET"],
        name="download_stream",
    )


_install_download_route()


class RateLimitedASGI:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowLimiter | None = None,
        public_limit: int | None = None,
    ) -> None:
        self.app = app
        self.limiter = limiter or SlidingWindowLimiter()
        self.public_limit = max(
            1,
            int(settings.rate_limit_per_minute if public_limit is None else public_limit),
        )

    @staticmethod
    def _client_key(scope: dict[str, Any]) -> str:
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and client:
            return str(client[0])
        return "unknown"

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if method == "POST" and path in {"/api/inspect", "/api/jobs"}:
            client = self._client_key(scope)
            allowed = self.limiter.allow(
                f"{path}:{client}",
                limit=self.public_limit,
                window_seconds=60,
            )
            if not allowed:
                body = json.dumps(
                    {"detail": "تم تجاوز حد الطلبات، حاول لاحقًا"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 429,
                        "headers": [
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"retry-after", b"60"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)


app = RateLimitedASGI(fastapi_app)
