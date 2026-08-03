from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

CALLBACK_VIDEO = "media:video"
CALLBACK_AUDIO = "media:audio"
CALLBACK_QUALITY_PREFIX = "media:q:"
TELEGRAM_CLOUD_LIMIT_BYTES = 49_000_000

_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


@dataclass(frozen=True)
class CallbackContext:
    chat_id: int
    preview_message_id: int
    owner_id: int
    url: str


@dataclass
class MediaMetadata:
    title: str
    thumbnail: str
    duration: float | None
    formats: list[dict[str, Any]]
    preview_url: str = ""


class TelegramAPIError(RuntimeError):
    pass


def extract_first_url(text: str) -> str:
    match = _URL_RE.search(text or "")
    if not match:
        raise ValueError("أرسل رابطًا يبدأ بـ http:// أو https://")
    return match.group(0).rstrip(".,;:!?)]}،؛")


def progress_bar(pct: int, width: int = 12) -> str:
    value = max(0, min(100, int(pct)))
    filled = round(value / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _rounded_step(pct: int | float) -> int:
    return max(0, min(100, int(float(pct) // 10 * 10)))


def type_buttons() -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "🎬 فيديو", "callback_data": CALLBACK_VIDEO},
            {"text": "🎧 صوت MP3", "callback_data": CALLBACK_AUDIO},
        ]]
    }


def _format_size_label(size: int | None) -> str:
    if not size:
        return ""
    mb = size / 1024 / 1024
    if mb < 10:
        return f" • ~{mb:.1f}MB"
    return f" • ~{mb:.0f}MB"


def quality_buttons(
    formats: list[dict[str, Any]],
    *,
    max_bytes: int = TELEGRAM_CLOUD_LIMIT_BYTES,
) -> dict[str, Any]:
    by_height: dict[int, int | None] = {}
    original_size: int | None = None
    for item in formats or []:
        raw_height = item.get("height")
        size_value = item.get("size")
        try:
            size = int(size_value) if size_value else None
        except (TypeError, ValueError):
            size = None

        if raw_height in (None, "", 0, "original"):
            if item.get("original"):
                if size is None or size <= max_bytes:
                    original_size = size
            continue

        try:
            height = int(raw_height)
        except (TypeError, ValueError):
            continue
        if height <= 0:
            continue
        if size is not None and size > max_bytes:
            continue

        previous = by_height.get(height)
        if previous is None:
            by_height[height] = size
        elif size is not None and (previous is None or size < previous):
            by_height[height] = size

    buttons: list[dict[str, str]] = []
    for height in sorted(by_height):
        size = by_height[height]
        buttons.append({
            "text": f"{height}p{_format_size_label(size)}",
            "callback_data": f"{CALLBACK_QUALITY_PREFIX}{height}",
        })

    if not buttons and original_size is not None:
        buttons.append({
            "text": f"الجودة الأصلية{_format_size_label(original_size)}",
            "callback_data": f"{CALLBACK_QUALITY_PREFIX}original",
        })

    rows: list[list[dict[str, str]]] = []
    for index in range(0, len(buttons), 2):
        rows.append(buttons[index:index + 2])
    return {"inline_keyboard": rows}


def callback_context(callback: dict[str, Any]) -> CallbackContext:
    message = callback.get("message") or {}
    reply = message.get("reply_to_message") or {}
    callback_user = (callback.get("from") or {}).get("id")
    owner_id = (reply.get("from") or {}).get("id")
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if not all(isinstance(value, int) for value in (callback_user, owner_id, chat_id, message_id)):
        raise ValueError("انتهت صلاحية هذا الاختيار؛ أرسل الرابط مرة أخرى")
    if callback_user != owner_id:
        raise PermissionError("هذا الاختيار لصاحب الرابط فقط")
    source_text = str(reply.get("text") or reply.get("caption") or "")
    return CallbackContext(
        chat_id=chat_id,
        preview_message_id=message_id,
        owner_id=owner_id,
        url=extract_first_url(source_text),
    )


def safe_media_filename(title: str, suffix: str) -> str:
    normalized = unicodedata.normalize("NFKC", title or "media")
    normalized = _INVALID_FILENAME_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized:
        normalized = "media"
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    max_stem = max(1, 120 - len(suffix))
    return f"{normalized[:max_stem].rstrip()}{suffix}"


def selected_format(callback_data: str) -> str:
    if not callback_data.startswith(CALLBACK_QUALITY_PREFIX):
        raise ValueError("اختيار جودة غير صالح")
    value = callback_data[len(CALLBACK_QUALITY_PREFIX):]
    if value == "original":
        return "best"
    if not value.isdigit() or not 144 <= int(value) <= 4320:
        raise ValueError("اختيار جودة غير صالح")
    height = int(value)
    return (
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]"
    )


def _estimated_bytes(item: dict[str, Any], duration: float | None) -> int | None:
    direct = item.get("filesize") or item.get("filesize_approx")
    if direct:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass
    tbr = item.get("tbr") or item.get("abr") or item.get("vbr")
    if tbr and duration:
        try:
            return int(float(tbr) * 1000 / 8 * float(duration))
        except (TypeError, ValueError):
            return None
    return None


def _metadata_from_info(info: dict[str, Any], max_bytes: int) -> MediaMetadata:
    duration_raw = info.get("duration")
    try:
        duration = float(duration_raw) if duration_raw is not None else None
    except (TypeError, ValueError):
        duration = None

    formats = list(info.get("formats") or [])
    audio_sizes = [
        _estimated_bytes(item, duration)
        for item in formats
        if item.get("vcodec") == "none" and item.get("acodec") not in (None, "none")
    ]
    audio_size = max((size for size in audio_sizes if size), default=0)

    by_height: dict[int, dict[str, Any]] = {}
    preview_url = ""
    for item in formats:
        if item.get("vcodec") in (None, "none"):
            continue
        try:
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        stream_url = str(item.get("url") or "")
        if stream_url and height and (not preview_url or height >= 360):
            preview_url = stream_url
        if height <= 0:
            continue
        video_size = _estimated_bytes(item, duration)
        combined = video_size
        if item.get("acodec") in (None, "none") and video_size:
            combined = video_size + audio_size
        candidate = {"height": height, "size": combined}
        previous = by_height.get(height)
        previous_size = (previous or {}).get("size")
        if previous is None:
            by_height[height] = candidate
        elif combined and (not previous_size or combined < previous_size):
            by_height[height] = candidate

    available = [
        item for _, item in sorted(by_height.items())
        if item.get("size") is None or int(item["size"]) <= max_bytes
    ]
    if not available:
        fallback_size = _estimated_bytes(info, duration)
        if fallback_size is None or fallback_size <= max_bytes:
            available = [{"height": "original", "size": fallback_size, "original": True}]
            preview_url = preview_url or str(info.get("url") or "")

    title = str(info.get("title") or "").strip() or "مقطع فيديو"
    return MediaMetadata(
        title=title,
        thumbnail=str(info.get("thumbnail") or ""),
        duration=duration,
        formats=available,
        preview_url=preview_url,
    )


def inspect_source(url: str, max_bytes: int = TELEGRAM_CLOUD_LIMIT_BYTES) -> MediaMetadata:
    from .config import settings
    from .extractor import UniversalExtractor
    from .security import validate_public_url

    clean_url = validate_public_url(url)
    extractor = UniversalExtractor(
        use_browser=False,
        max_filesize=max_bytes,
        max_duration=settings.max_duration_seconds,
        request_timeout=settings.request_timeout_seconds,
    )
    try:
        try:
            info = extractor._ytdlp_info(clean_url)
            return _metadata_from_info(info, max_bytes)
        except Exception:
            result = extractor.inspect(clean_url)
            parsed: list[dict[str, Any]] = []
            for item in result.get("formats") or []:
                label = str(item.get("label") or "")
                match = re.search(r"(\d{3,4})p", label)
                if match:
                    parsed.append({
                        "height": int(match.group(1)),
                        "size": item.get("size"),
                    })
            if not parsed:
                parsed = [{"height": "original", "size": None, "original": True}]
            return MediaMetadata(
                title=str(result.get("title") or "مقطع فيديو"),
                thumbnail=str(result.get("thumbnail") or ""),
                duration=result.get("duration"),
                formats=parsed,
                preview_url="",
            )
    finally:
        extractor.cleanup()


class _ProgressReader:
    def __init__(self, file_object, total: int, callback: Callable[[int, int], None] | None):
        self._file = file_object
        self._total = max(0, int(total))
        self._callback = callback
        self._done = int(file_object.tell() or 0)

    def read(self, size: int = -1):
        data = self._file.read(size)
        self._done += len(data)
        if self._callback:
            self._callback(self._done, self._total)
        return data

    def seek(self, offset: int, whence: int = os.SEEK_SET):
        position = self._file.seek(offset, whence)
        self._done = int(position)
        return position

    def tell(self):
        return self._file.tell()

    def fileno(self):
        return self._file.fileno()

    def readable(self):
        return True

    def seekable(self):
        return True

    def __len__(self):
        return self._total

    def __getattr__(self, name):
        return getattr(self._file, name)


class TelegramAPI:
    def __init__(self, token: str):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN غير مضبوط")
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        import httpx

        timeout = httpx.Timeout(60.0, connect=20.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(f"{self.base_url}/{method}", json=payload or {})
        try:
            body = response.json()
        except Exception as exc:
            raise TelegramAPIError(f"Telegram HTTP {response.status_code}") from exc
        if response.status_code >= 400 or not body.get("ok"):
            description = str(body.get("description") or "Telegram request failed")
            raise TelegramAPIError(description[:240])
        return body.get("result")

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            payload["reply_parameters"] = {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self.call("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        await self.call("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        })

    async def edit_caption(
        self,
        chat_id: int,
        message_id: int,
        caption: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": caption,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self.call("editMessageCaption", payload)

    async def edit_markup(self, chat_id: int, message_id: int, reply_markup: dict[str, Any]) -> None:
        await self.call("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        })

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except TelegramAPIError:
            pass

    async def answer_callback(self, callback_id: str, text: str = "", *, show_alert: bool = False) -> None:
        payload: dict[str, Any] = {
            "callback_query_id": callback_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text[:200]
        try:
            await self.call("answerCallbackQuery", payload)
        except TelegramAPIError:
            pass

    async def send_preview(
        self,
        chat_id: int,
        reply_to_message_id: int,
        metadata: MediaMetadata,
        source_url: str,
    ) -> dict[str, Any]:
        import httpx

        payload = {
            "chat_id": chat_id,
            "photo": metadata.thumbnail,
            "reply_parameters": {
                "message_id": reply_to_message_id,
                "allow_sending_without_reply": True,
            },
            "reply_markup": type_buttons(),
        }
        if metadata.thumbnail:
            try:
                result = await self.call("sendPhoto", payload)
                return result if isinstance(result, dict) else {}
            except TelegramAPIError:
                pass

        preview_dir = Path(tempfile.mkdtemp(prefix="yjpreview-", dir="/tmp"))
        preview_path = preview_dir / "preview.jpg"
        try:
            await asyncio.to_thread(_create_preview_frame, metadata.preview_url or source_url, preview_path)
            data = {
                "chat_id": str(chat_id),
                "reply_parameters": json.dumps({
                    "message_id": reply_to_message_id,
                    "allow_sending_without_reply": True,
                }, separators=(",", ":")),
                "reply_markup": json.dumps(type_buttons(), ensure_ascii=False, separators=(",", ":")),
            }
            timeout = httpx.Timeout(60.0, connect=20.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                with preview_path.open("rb") as handle:
                    response = await client.post(
                        f"{self.base_url}/sendPhoto",
                        data=data,
                        files={"photo": ("preview.jpg", handle, "image/jpeg")},
                    )
            body = response.json()
            if response.status_code >= 400 or not body.get("ok"):
                raise TelegramAPIError(str(body.get("description") or "تعذر إرسال المعاينة"))
            result = body.get("result")
            return result if isinstance(result, dict) else {}
        finally:
            shutil.rmtree(preview_dir, ignore_errors=True)

    def _upload_sync(
        self,
        *,
        method: str,
        field: str,
        chat_id: int,
        path: Path,
        filename: str,
        mime: str,
        callback: Callable[[int, int], None] | None,
    ) -> dict[str, Any]:
        import httpx

        total = path.stat().st_size
        with path.open("rb") as raw:
            reader = _ProgressReader(raw, total, callback)
            data: dict[str, str] = {"chat_id": str(chat_id)}
            if method == "sendVideo":
                data["supports_streaming"] = "true"
            timeout = httpx.Timeout(600.0, connect=30.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    f"{self.base_url}/{method}",
                    data=data,
                    files={field: (filename, reader, mime)},
                )
        try:
            body = response.json()
        except Exception as exc:
            raise TelegramAPIError(f"Telegram HTTP {response.status_code}") from exc
        if response.status_code >= 400 or not body.get("ok"):
            raise TelegramAPIError(str(body.get("description") or "فشل رفع الملف")[:240])
        result = body.get("result")
        return result if isinstance(result, dict) else {}

    async def upload_media(
        self,
        *,
        chat_id: int,
        path: Path,
        media_type: str,
        title: str,
        progress_message_id: int,
    ) -> dict[str, Any]:
        size = path.stat().st_size
        if size > TELEGRAM_CLOUD_LIMIT_BYTES:
            raise TelegramAPIError("حجم الملف يتجاوز حد الإرسال المباشر في مشروع Vercel فقط")
        state = {"pct": 0}

        def callback(done: int, total: int) -> None:
            state["pct"] = int(done / total * 100) if total else 0

        if media_type == "audio":
            method, field, mime = "sendAudio", "audio", "audio/mpeg"
            filename = safe_media_filename(title, ".mp3")
        else:
            method, field, mime = "sendVideo", "video", "video/mp4"
            filename = safe_media_filename(title, ".mp4")

        task = asyncio.create_task(asyncio.to_thread(
            self._upload_sync,
            method=method,
            field=field,
            chat_id=chat_id,
            path=path,
            filename=filename,
            mime=mime,
            callback=callback,
        ))
        last = -10
        while not task.done():
            step = _rounded_step(state["pct"])
            if step >= last + 10:
                await _safe_edit(
                    self,
                    chat_id,
                    progress_message_id,
                    f"⬆️ جارٍ الرفع [{progress_bar(step)}] {step}%",
                )
                last = step
            await asyncio.sleep(1)
        result = await task
        await _safe_edit(
            self,
            chat_id,
            progress_message_id,
            f"⬆️ جارٍ الرفع [{progress_bar(100)}] 100%",
        )
        return result


def _create_preview_frame(source: str, output: Path) -> None:
    from .config import ffmpeg_location

    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_location()
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-ss", "1", "-i", source,
        "-frames:v", "1",
        "-vf", "scale='min(1280,iw)':-2",
        "-q:v", "4",
        str(output),
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=45, check=True)
    except Exception:
        subprocess.run([
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=0x20242b:s=1280x720",
            "-frames:v", "1", str(output),
        ], capture_output=True, timeout=20, check=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("تعذر إنشاء صورة المعاينة")


async def _safe_edit(api: TelegramAPI, chat_id: int, message_id: int, text: str) -> None:
    try:
        await api.edit_text(chat_id, message_id, text)
    except TelegramAPIError:
        pass


async def _animate_task(*, task: asyncio.Task, update: Callable[[int], Any]) -> Any:
    pct = 0
    await update(0)
    while not task.done():
        await asyncio.sleep(1)
        pct = min(90, pct + 10)
        await update(pct)
    result = await task
    await update(100)
    return result


def _download_media(
    url: str,
    temp_dir: Path,
    *,
    media_type: str,
    quality: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    from .config import settings
    from .extractor import UniversalExtractor

    def hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            state["stage"] = "downloading"
            state["pct"] = int(done / total * 100) if total else 0
        elif status in ("finished", "post-processing"):
            state["stage"] = "preparing"
            state["pct"] = 0

    extractor = UniversalExtractor(
        use_browser=False,
        progress_hook=hook,
        max_filesize=TELEGRAM_CLOUD_LIMIT_BYTES,
        max_duration=settings.max_duration_seconds,
        request_timeout=settings.request_timeout_seconds,
    )
    preferred = "audio" if media_type == "audio" else quality
    try:
        return extractor.download(url, temp_dir, preferred=preferred)
    finally:
        extractor.cleanup()


def _probe_duration(path: Path) -> float:
    from .config import ffmpeg_location

    ffprobe = str(Path(ffmpeg_location()).with_name("ffprobe"))
    result = subprocess.run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], capture_output=True, text=True, timeout=30)
    try:
        return max(0.1, float(result.stdout.strip()))
    except (TypeError, ValueError):
        return 0.1


def _convert_to_mp3(source: Path, destination: Path, state: dict[str, Any]) -> None:
    from .config import ffmpeg_location

    duration = _probe_duration(source)
    process = subprocess.Popen([
        ffmpeg_location(), "-y", "-i", str(source),
        "-vn", "-codec:a", "libmp3lame", "-q:a", "2",
        "-map_metadata", "0",
        "-progress", "pipe:1", "-nostats",
        str(destination),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    assert process.stdout is not None
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        if key in {"out_time_ms", "out_time_us"}:
            try:
                seconds = int(value) / 1_000_000
            except ValueError:
                continue
            state["pct"] = int(seconds / duration * 100)
    _, stderr = process.communicate(timeout=30)
    if process.returncode != 0:
        raise RuntimeError((stderr or "فشل تحويل الصوت")[-300:])
    state["pct"] = 100


async def process_media(
    api: TelegramAPI,
    *,
    chat_id: int,
    url: str,
    media_type: str,
    quality: str,
    title_hint: str = "مقطع فيديو",
) -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="yjdownload-", dir="/tmp"))
    progress = await api.send_message(chat_id, f"⬇️ جارٍ التحميل [{progress_bar(0)}] 0%")
    progress_id = int(progress.get("message_id") or 0)
    state: dict[str, Any] = {"stage": "downloading", "pct": 0}
    success = False
    try:
        download_task = asyncio.create_task(asyncio.to_thread(
            _download_media,
            url,
            temp_dir,
            media_type=media_type,
            quality=quality,
            state=state,
        ))
        last_stage = ""
        last_step = -10
        preparing_step = 0
        while not download_task.done():
            stage = str(state.get("stage") or "downloading")
            if stage == "preparing":
                preparing_step = min(90, preparing_step + 10) if last_stage == "preparing" else 0
                step = preparing_step
                text = f"🔧 جارٍ التجهيز [{progress_bar(step)}] {step}%"
            else:
                step = _rounded_step(state.get("pct") or 0)
                text = f"⬇️ جارٍ التحميل [{progress_bar(step)}] {step}%"
            if stage != last_stage or step >= last_step + 10:
                if progress_id:
                    await _safe_edit(api, chat_id, progress_id, text)
                last_stage, last_step = stage, step
            await asyncio.sleep(1)
        result = await download_task
        source_path = Path(str(result["path"]))
        title = str(result.get("title") or title_hint or "مقطع فيديو")

        if media_type == "audio":
            output_path = temp_dir / safe_media_filename(title, ".mp3")
            conversion = {"pct": 0}
            conversion_task = asyncio.create_task(asyncio.to_thread(
                _convert_to_mp3,
                source_path,
                output_path,
                conversion,
            ))
            last = -10
            while not conversion_task.done():
                step = _rounded_step(conversion["pct"])
                if step >= last + 10 and progress_id:
                    await _safe_edit(
                        api,
                        chat_id,
                        progress_id,
                        f"🎧 جارٍ التحويل [{progress_bar(step)}] {step}%",
                    )
                    last = step
                await asyncio.sleep(1)
            await conversion_task
            source_path = output_path
            if progress_id:
                await _safe_edit(
                    api,
                    chat_id,
                    progress_id,
                    f"🎧 جارٍ التحويل [{progress_bar(100)}] 100%",
                )
        elif progress_id:
            await _safe_edit(
                api,
                chat_id,
                progress_id,
                f"🔧 جارٍ التجهيز [{progress_bar(100)}] 100%",
            )

        if source_path.stat().st_size > TELEGRAM_CLOUD_LIMIT_BYTES:
            raise RuntimeError("الجودة المختارة أكبر من حد الإرسال المباشر في Vercel؛ اختر جودة أقل")
        await api.upload_media(
            chat_id=chat_id,
            path=source_path,
            media_type=media_type,
            title=title,
            progress_message_id=progress_id,
        )
        success = True
    except Exception as exc:
        if progress_id:
            await _safe_edit(api, chat_id, progress_id, f"❌ تعذرت المعالجة: {str(exc)[:220]}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if success and progress_id:
            await api.delete_message(chat_id, progress_id)


def cleanup_vercel_temp() -> None:
    roots = [Path("/tmp")]
    try:
        from .config import settings
        roots.append(settings.storage_path)
    except Exception:
        pass
    for root in roots:
        if not root.exists():
            continue
        for pattern in ("yjdownload-*", "yjpreview-*"):
            for path in root.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)


def retained_media_stats() -> tuple[int, int]:
    count = 0
    total = 0
    root = Path("/tmp")
    for pattern in ("yjdownload-*", "yjpreview-*"):
        for path in root.glob(pattern):
            if path.is_file():
                count += 1
                total += path.stat().st_size
            elif path.is_dir():
                for file in path.rglob("*"):
                    if file.is_file():
                        count += 1
                        total += file.stat().st_size
    return count, total


async def handle_update(data: dict[str, Any]) -> dict[str, Any]:
    from .config import settings
    from .security import validate_public_url

    api = TelegramAPI(settings.telegram_bot_token)
    message = data.get("message") or data.get("edited_message")
    if isinstance(message, dict):
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        text = str(message.get("text") or "").strip()
        if not isinstance(chat_id, int):
            return {"ok": True, "delivery": "ignored"}
        if text.startswith(("/start", "/help")):
            await api.send_message(
                chat_id,
                (
                    "أرسل رابط المقطع، ثم اختر 🎬 فيديو أو 🎧 صوت MP3. "
                    "عند اختيار الفيديو سأعرض الجودات الموجودة والقابلة للإرسال مباشرة."
                ),
                reply_to_message_id=message_id if isinstance(message_id, int) else None,
            )
            return {"ok": True, "delivery": "command"}
        try:
            url = validate_public_url(extract_first_url(text))
        except ValueError as exc:
            await api.send_message(
                chat_id,
                f"⚠️ {exc}",
                reply_to_message_id=message_id if isinstance(message_id, int) else None,
            )
            return {"ok": True, "delivery": "invalid"}

        progress = await api.send_message(
            chat_id,
            f"🔎 جارٍ فحص الرابط [{progress_bar(0)}] 0%",
            reply_to_message_id=message_id if isinstance(message_id, int) else None,
        )
        progress_id = int(progress.get("message_id") or 0)
        inspect_task = asyncio.create_task(asyncio.to_thread(inspect_source, url))

        async def update_inspect(pct: int) -> None:
            if progress_id:
                await _safe_edit(
                    api,
                    chat_id,
                    progress_id,
                    f"🔎 جارٍ فحص الرابط [{progress_bar(pct)}] {pct}%",
                )

        try:
            metadata = await _animate_task(task=inspect_task, update=update_inspect)
            await api.send_preview(chat_id, int(message_id), metadata, url)
            if progress_id:
                await api.delete_message(chat_id, progress_id)
            return {"ok": True, "delivery": "choice"}
        except Exception as exc:
            if progress_id:
                await _safe_edit(api, chat_id, progress_id, f"❌ تعذر قراءة المقطع: {str(exc)[:220]}")
            return {"ok": True, "delivery": "failed"}

    callback = data.get("callback_query")
    if not isinstance(callback, dict):
        return {"ok": True, "delivery": "ignored"}

    callback_id = str(callback.get("id") or "")
    callback_data = str(callback.get("data") or "")
    try:
        context = callback_context(callback)
    except PermissionError as exc:
        await api.answer_callback(callback_id, str(exc), show_alert=True)
        return {"ok": True, "delivery": "forbidden"}
    except ValueError as exc:
        await api.answer_callback(callback_id, str(exc), show_alert=True)
        return {"ok": True, "delivery": "expired"}

    if callback_data == CALLBACK_VIDEO:
        await api.answer_callback(callback_id, "🔎 جارٍ قراءة الجودات…")
        await api.edit_markup(context.chat_id, context.preview_message_id, {"inline_keyboard": []})
        inspect_task = asyncio.create_task(asyncio.to_thread(inspect_source, context.url))

        async def update_caption(pct: int) -> None:
            try:
                await api.edit_caption(
                    context.chat_id,
                    context.preview_message_id,
                    f"🔎 جارٍ قراءة الجودات [{progress_bar(pct)}] {pct}%",
                    reply_markup={"inline_keyboard": []},
                )
            except TelegramAPIError:
                pass

        try:
            metadata = await _animate_task(task=inspect_task, update=update_caption)
            keyboard = quality_buttons(metadata.formats)
            if not keyboard["inline_keyboard"]:
                await api.edit_caption(
                    context.chat_id,
                    context.preview_message_id,
                    "❌ لا توجد جودة ضمن حد الإرسال المباشر؛ اختر الصوت أو أرسل مقطعًا أصغر",
                    reply_markup=type_buttons(),
                )
                return {"ok": True, "delivery": "no_quality"}
            await api.edit_caption(
                context.chat_id,
                context.preview_message_id,
                "",
                reply_markup=keyboard,
            )
            return {"ok": True, "delivery": "qualities"}
        except Exception as exc:
            await api.edit_caption(
                context.chat_id,
                context.preview_message_id,
                f"❌ تعذر قراءة الجودات: {str(exc)[:180]}",
                reply_markup=type_buttons(),
            )
            return {"ok": True, "delivery": "failed"}

    if callback_data == CALLBACK_AUDIO:
        await api.answer_callback(callback_id, "🎧 بدأ تجهيز الصوت")
        await api.delete_message(context.chat_id, context.preview_message_id)
        await process_media(
            api,
            chat_id=context.chat_id,
            url=context.url,
            media_type="audio",
            quality="audio",
        )
        return {"ok": True, "delivery": "audio"}

    if callback_data.startswith(CALLBACK_QUALITY_PREFIX):
        try:
            quality = selected_format(callback_data)
        except ValueError as exc:
            await api.answer_callback(callback_id, str(exc), show_alert=True)
            return {"ok": True, "delivery": "invalid_quality"}
        await api.answer_callback(callback_id, "⬇️ بدأ تحميل الفيديو")
        await api.delete_message(context.chat_id, context.preview_message_id)
        await process_media(
            api,
            chat_id=context.chat_id,
            url=context.url,
            media_type="video",
            quality=quality,
        )
        return {"ok": True, "delivery": "video"}

    await api.answer_callback(callback_id)
    return {"ok": True, "delivery": "ignored"}
