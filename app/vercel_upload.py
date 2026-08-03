from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .config import settings

MAX_BOT_UPLOAD_BYTES = 49 * 1024 * 1024


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
    from curl_cffi import CurlMime
    from curl_cffi import requests as curl_requests

    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    mime = CurlMime()
    mime.addpart(
        name=field_name,
        content_type="video/mp4",
        filename="video.mp4",
        local_path=str(media_path),
    )
    form = {"chat_id": str(chat_id), "caption": caption}
    if method == "sendVideo":
        form["supports_streaming"] = "true"

    try:
        response = curl_requests.post(
            endpoint,
            data=form,
            multipart=mime,
            timeout=120,
            http_version="v1",
        )
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Telegram returned HTTP {response.status_code}") from exc
        return parse_bot_api_response(body)
    finally:
        close = getattr(mime, "close", None)
        if callable(close):
            close()


def download_and_upload(chat_id: int, source_url: str, user_id: int | None) -> dict:
    """Download and upload in one Vercel invocation using libcurl multipart."""
    workdir = Path(tempfile.mkdtemp(prefix="telegram-upload-", dir=str(settings.storage_path)))
    try:
        media_path, title = _download_media(source_url, user_id, workdir)
        try:
            return _upload_once("sendVideo", "video", chat_id, media_path, title)
        except RuntimeError as video_error:
            try:
                return _upload_once("sendDocument", "document", chat_id, media_path, title)
            except RuntimeError as document_error:
                raise RuntimeError(
                    f"فشل رفع الفيديو: {str(video_error)[:100]} | {str(document_error)[:100]}"
                ) from document_error
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
