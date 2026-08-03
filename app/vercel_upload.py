from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

from .config import settings

MAX_BOT_UPLOAD_BYTES = 49 * 1024 * 1024
log = logging.getLogger("vercel-upload")


class TelegramUploadTimeout(RuntimeError):
    """The Bot API did not finish receiving/responding within the safe window."""


def upload_plan() -> list[tuple[str, str]]:
    """Prefer documents because Telegram can acknowledge them without video processing."""
    return [
        ("sendDocument", "document"),
        ("sendVideo", "video"),
    ]


def select_downloaded_file(directory: Path, max_bytes: int = MAX_BOT_UPLOAD_BYTES) -> Path:
    """Return the completed media file and reject partial or oversized output."""
    candidates = [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and not path.name.endswith((".part", ".ytdl", ".temp"))
        and path.stat().st_size > 0
    ]
    if not candidates:
        raise RuntimeError("لم يتم إنشاء ملف فيديو مكتمل")

    selected = max(candidates, key=lambda path: path.stat().st_size)
    if selected.stat().st_size > max_bytes:
        raise RuntimeError("حجم الفيديو يتجاوز حد تيليجرام البالغ 50MB")
    return selected


def parse_bot_api_response(body: dict) -> dict:
    """Return a successful Bot API result or raise a short safe error."""
    if body.get("ok"):
        result = body.get("result")
        return result if isinstance(result, dict) else {}
    description = str(body.get("description") or "Telegram upload failed")
    raise RuntimeError(description[:240])


def _cookie_file(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    candidate = settings.cookies_path / f"tg:{user_id}.txt"
    return str(candidate) if candidate.is_file() else None


def _download_media(source_url: str, user_id: int | None, workdir: Path) -> tuple[Path, str]:
    from yt_dlp import YoutubeDL

    browser_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
    )
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": MAX_BOT_UPLOAD_BYTES,
        "format": (
            "best[ext=mp4][vcodec!=none][acodec!=none][filesize<49M]/"
            "best[ext=mp4][vcodec!=none][acodec!=none][filesize_approx<49M]/"
            "best[ext=mp4][vcodec!=none][acodec!=none]"
        ),
        "outtmpl": str(workdir / "video.%(ext)s"),
        "http_headers": {"User-Agent": browser_user_agent},
    }
    cookie_file = _cookie_file(user_id)
    if cookie_file:
        options["cookiefile"] = cookie_file

    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(source_url, download=True)

    if info and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    title = str((info or {}).get("title") or "الفيديو")[:1024]
    return select_downloaded_file(workdir), title


def _upload_once(method: str, field_name: str, chat_id: int, media_path: Path, caption: str) -> dict:
    import requests

    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    form = {"chat_id": str(chat_id), "caption": caption}
    if method == "sendVideo":
        form["supports_streaming"] = "true"

    size_bytes = media_path.stat().st_size
    started = time.monotonic()
    log.info(
        "telegram upload started method=%s size_bytes=%d",
        method,
        size_bytes,
    )

    try:
        with media_path.open("rb") as media_file:
            response = requests.post(
                endpoint,
                data=form,
                files={
                    field_name: (
                        "video.mp4",
                        media_file,
                        "video/mp4",
                    )
                },
                headers={
                    "Connection": "close",
                    "Expect": "",
                    "User-Agent": "YJDownloadVideo/1.0",
                },
                timeout=(20, 270),
            )
    except requests.exceptions.Timeout as exc:
        elapsed = time.monotonic() - started
        log.warning(
            "telegram upload timed out method=%s size_bytes=%d elapsed_seconds=%.1f",
            method,
            size_bytes,
            elapsed,
        )
        raise TelegramUploadTimeout(
            f"انتهت مهلة رفع ملف حجمه {size_bytes / 1024 / 1024:.1f}MB بعد {elapsed:.0f} ثانية"
        ) from exc
    except requests.exceptions.RequestException as exc:
        elapsed = time.monotonic() - started
        log.warning(
            "telegram upload network error method=%s size_bytes=%d elapsed_seconds=%.1f error=%s",
            method,
            size_bytes,
            elapsed,
            exc.__class__.__name__,
        )
        raise RuntimeError(f"تعذر الاتصال بخادم تيليجرام: {exc.__class__.__name__}") from exc

    elapsed = time.monotonic() - started
    log.info(
        "telegram upload response method=%s size_bytes=%d elapsed_seconds=%.1f status_code=%d",
        method,
        size_bytes,
        elapsed,
        response.status_code,
    )
    try:
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Telegram returned HTTP {response.status_code}") from exc
    return parse_bot_api_response(body)


def download_and_upload(chat_id: int, source_url: str, user_id: int | None) -> dict:
    """Download and upload inside one bounded Vercel invocation."""
    workdir = Path(tempfile.mkdtemp(prefix="telegram-upload-", dir=str(settings.storage_path)))
    download_started = time.monotonic()
    try:
        media_path, title = _download_media(source_url, user_id, workdir)
        size_bytes = media_path.stat().st_size
        download_elapsed = time.monotonic() - download_started
        log.info(
            "telegram media ready size_bytes=%d download_seconds=%.1f",
            size_bytes,
            download_elapsed,
        )

        first_error: RuntimeError | None = None
        for method, field_name in upload_plan():
            try:
                return _upload_once(method, field_name, chat_id, media_path, title)
            except TelegramUploadTimeout:
                # A second full upload would exceed the Vercel execution window.
                raise
            except RuntimeError as exc:
                first_error = exc
                log.warning("telegram upload method failed method=%s error=%s", method, str(exc)[:160])

        raise RuntimeError(f"فشل رفع الفيديو: {str(first_error or 'خطأ غير معروف')[:180]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
