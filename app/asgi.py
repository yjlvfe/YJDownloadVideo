from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from .config import settings
from .db import Job, SessionLocal
from .main import app as fastapi_app
from .media_choice import safe_media_title
from .rate_limit import SlidingWindowLimiter
from .redaction import sanitize_external_error
from .security import read_token
from .telegram_flow import TelegramFlow


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]


_original_safe_error = TelegramFlow._safe_error


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


def media_response_metadata(path: str | Path, title: str) -> tuple[str, str]:
    media_path = Path(path)
    suffix = media_path.suffix or ".bin"
    filename = safe_media_title(title or media_path.stem or "media", suffix)
    media_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
    return filename, media_type


def _install_download_route() -> None:
    # Replace the compatibility route from app.main so responses always use the
    # actual output extension and MIME type (MP4, M4A, MP3, WebM, etc.).
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
