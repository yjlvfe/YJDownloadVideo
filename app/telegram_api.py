from __future__ import annotations

import asyncio
import json
import mimetypes
from contextlib import ExitStack
from pathlib import Path
from typing import Any, BinaryIO, Callable

import httpx

from .video_delivery import VideoUploadMetadata, prepare_video_upload


ProgressCallback = Callable[[int, int], None]


class ProgressFile:
    def __init__(
        self,
        raw: BinaryIO,
        *,
        total: int,
        callback: ProgressCallback | None = None,
    ) -> None:
        self.raw = raw
        self.total = max(0, int(total))
        self.callback = callback
        self.done = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.raw.read(size)
        self.done += len(chunk)
        if self.callback is not None:
            self.callback(self.done, self.total)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self.raw.seek(offset, whence)
        self.done = max(0, int(position))
        return position

    def tell(self) -> int:
        return self.raw.tell()

    def fileno(self) -> int:
        return self.raw.fileno()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw, name)


def media_payload(
    kind: str,
    *,
    chat_id: int,
    title: str = "",
    video_metadata: VideoUploadMetadata | None = None,
) -> dict[str, Any]:
    if kind == "video":
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "supports_streaming": "true",
        }
        metadata = video_metadata or VideoUploadMetadata()
        if metadata.duration:
            payload["duration"] = metadata.duration
        if metadata.width:
            payload["width"] = metadata.width
        if metadata.height:
            payload["height"] = metadata.height
        return payload
    if kind == "audio":
        return {"chat_id": chat_id, "title": title[:64]}
    raise ValueError("unsupported media kind")


class TelegramAPIError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        timeout_seconds: float = 600.0,
    ) -> None:
        if not token:
            raise ValueError("Telegram bot token is required")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(30.0, float(timeout_seconds))

    def endpoint(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    async def ready(self) -> bool:
        """Check that the local Bot API listener is accepting HTTP connections.

        This deliberately avoids authenticated methods such as getMe so startup and
        health probes cannot consume Telegram's per-bot request quota.
        """
        timeout = httpx.Timeout(3.0, connect=2.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                await client.get(f"{self.base_url}/")
            return True
        except httpx.HTTPError:
            return False

    @staticmethod
    def _result(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise TelegramAPIError(f"Telegram returned HTTP {response.status_code}") from exc
        if response.status_code >= 400 or not body.get("ok"):
            reason = str(body.get("description") or f"HTTP {response.status_code}")
            raise TelegramAPIError(reason[:240])
        result = body.get("result")
        return result if isinstance(result, dict) else {}

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = httpx.Timeout(self.timeout_seconds, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(self.endpoint(method), json=payload)
        return self._result(response)

    async def send_message(self, chat_id: int, text: str, **extra: Any) -> dict[str, Any]:
        return await self.call("sendMessage", {"chat_id": chat_id, "text": text, **extra})

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        **extra: Any,
    ) -> dict[str, Any]:
        return await self.call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, **extra},
        )

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            },
        )

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
        *,
        show_alert: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        await self.call("answerCallbackQuery", payload)

    async def send_photo(
        self,
        chat_id: int,
        photo: str | Path,
        *,
        caption: str,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(photo, str) and photo.startswith(("http://", "https://")):
            return await self.call(
                "sendPhoto",
                {
                    "chat_id": chat_id,
                    "photo": photo,
                    "caption": caption[:1024],
                    "reply_markup": reply_markup,
                },
            )
        return await self._upload(
            "sendPhoto",
            "photo",
            Path(photo),
            {
                "chat_id": chat_id,
                "caption": caption[:1024],
                "reply_markup": reply_markup,
            },
        )

    async def send_video_file(
        self,
        chat_id: int,
        path: str | Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        media_path = Path(path)
        metadata = await asyncio.to_thread(prepare_video_upload, media_path)
        extra_files = (
            {"thumbnail": metadata.thumbnail}
            if metadata.thumbnail is not None
            else None
        )
        try:
            return await self._upload(
                "sendVideo",
                "video",
                media_path,
                media_payload(
                    "video",
                    chat_id=chat_id,
                    video_metadata=metadata,
                ),
                progress=progress,
                extra_files=extra_files,
            )
        finally:
            if metadata.thumbnail is not None:
                metadata.thumbnail.unlink(missing_ok=True)

    async def send_audio_file(
        self,
        chat_id: int,
        path: str | Path,
        *,
        title: str,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        return await self._upload(
            "sendAudio",
            "audio",
            Path(path),
            media_payload("audio", chat_id=chat_id, title=title),
            progress=progress,
        )

    async def _upload(
        self,
        method: str,
        field: str,
        path: Path,
        payload: dict[str, Any],
        *,
        progress: ProgressCallback | None = None,
        extra_files: dict[str, Path] | None = None,
    ) -> dict[str, Any]:
        if not path.is_file():
            raise TelegramAPIError("Media file is missing")

        attachments = {
            name: Path(value)
            for name, value in (extra_files or {}).items()
            if Path(value).is_file()
        }

        def run() -> dict[str, Any]:
            data: dict[str, str] = {}
            for key, value in payload.items():
                if isinstance(value, (dict, list)):
                    data[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                elif isinstance(value, bool):
                    data[key] = "true" if value else "false"
                else:
                    data[key] = str(value)

            with ExitStack() as stack:
                raw = stack.enter_context(path.open("rb"))
                wrapped = ProgressFile(raw, total=path.stat().st_size, callback=progress)
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                files: dict[str, tuple[str, Any, str]] = {
                    field: (path.name, wrapped, mime),
                }
                for name, attachment in attachments.items():
                    handle = stack.enter_context(attachment.open("rb"))
                    attachment_mime = (
                        mimetypes.guess_type(attachment.name)[0]
                        or "application/octet-stream"
                    )
                    files[name] = (attachment.name, handle, attachment_mime)

                timeout = httpx.Timeout(self.timeout_seconds, connect=30.0)
                with httpx.Client(timeout=timeout, trust_env=False) as client:
                    response = client.post(
                        self.endpoint(method),
                        data=data,
                        files=files,
                    )
            return self._result(response)

        return await asyncio.to_thread(run)