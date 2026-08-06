from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .media_choice import (
    QualityOption,
    SelectionStore,
    cleanup_path,
    is_quality_key,
    normalize_qualities,
    progress_step,
    safe_media_title,
    selector_for_quality,
)
from .telegram_api import TelegramAPI


_CALLBACK_RE = re.compile(
    r"^pick:(?:(v|a):([A-Za-z0-9_-]{6,24})|q:([A-Za-z0-9_-]{6,24}):([A-Za-z0-9_-]{1,16}))$"
)


def kind_keyboard(token: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "🎬 فيديو", "callback_data": f"pick:v:{token}"},
            {"text": "🎧 صوت MP3", "callback_data": f"pick:a:{token}"},
        ]]
    }


def quality_keyboard(token: str, qualities: list[QualityOption]) -> dict[str, Any]:
    buttons = [
        {
            "text": _quality_label(item),
            "callback_data": f"pick:q:{token}:{item.key}",
        }
        for item in qualities
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    return {"inline_keyboard": rows}


def _quality_label(item: QualityOption) -> str:
    if not item.size:
        return item.label
    mib = item.size / 1024 / 1024
    return f"{item.label} • ~{mib:.0f}MB"


def _best_quality() -> QualityOption:
    return QualityOption(
        key="best",
        label="⭐ الأفضل",
        selector=selector_for_quality("best"),
        size=None,
    )


def _supported_callback_quality(key: str) -> bool:
    return is_quality_key(key)


def parse_callback(value: str) -> tuple[str, str, str | None] | None:
    match = _CALLBACK_RE.fullmatch(str(value or ""))
    if not match:
        return None
    simple_kind, simple_token, quality_token, quality_key = match.groups()
    if simple_kind == "v":
        return ("video", simple_token, None)
    if simple_kind == "a":
        return ("audio", simple_token, None)
    if not quality_key or not _supported_callback_quality(quality_key):
        return None
    return ("quality", quality_token, quality_key)


def _progress_bar(pct: int, width: int = 12) -> str:
    bounded = max(0, min(100, int(pct)))
    filled = round(bounded / 100 * width)
    return "█" * filled + "░" * (width - filled)


def progress_text(stage: str, pct: int) -> str:
    step = progress_step(pct)
    labels = {
        "inspecting": "🔎 جارٍ فحص الرابط",
        "downloading": "⬇️ جارٍ التحميل",
        "preparing": "🔧 جارٍ تجهيز الفيديو",
        "converting": "🎧 جارٍ التحويل إلى MP3",
        "uploading": "⬆️ جارٍ الرفع",
    }
    label = labels.get(stage, "⏳ جارٍ المعالجة")
    return f"{label} [{_progress_bar(step)}] {step}%"


@dataclass(slots=True)
class DownloadRequest:
    chat_id: int
    owner_id: int
    url: str
    title: str
    kind: str
    selector: str
    status_message_id: int


class _ProgressState:
    def __init__(self, stage: str = "downloading", pct: int = 0) -> None:
        self._lock = threading.Lock()
        self._stage = stage
        self._pct = pct

    def set(self, stage: str, pct: int) -> None:
        with self._lock:
            self._stage = stage
            self._pct = max(0, min(100, int(pct)))

    def get(self) -> tuple[str, int]:
        with self._lock:
            return self._stage, self._pct


class TelegramFlow:
    def __init__(self, *, api: TelegramAPI, settings: Any) -> None:
        self.api = api
        self.settings = settings
        self.selections = SelectionStore(ttl_seconds=15 * 60)
        self.queue: asyncio.Queue[DownloadRequest] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._active = False
        self._seen_updates: set[int] = set()
        self._seen_order: deque[int] = deque(maxlen=2048)
        self.clear_runtime_storage()

    @property
    def queue_depth(self) -> int:
        return self.queue.qsize() + (1 if self._active else 0)

    @property
    def selection_count(self) -> int:
        return self.selections.count

    def clear_runtime_storage(self) -> None:
        root = Path(self.settings.storage_path)
        root.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            cleanup_path(child)

    async def handle_update(self, data: dict[str, Any]) -> None:
        update_id = data.get("update_id")
        if isinstance(update_id, int) and self._is_duplicate(update_id):
            return

        callback = data.get("callback_query")
        if isinstance(callback, dict):
            await self._handle_callback(callback)
            return

        message = data.get("message") or data.get("edited_message")
        if not isinstance(message, dict):
            return
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (message.get("from") or {}).get("id")
        text = str(message.get("text") or "").strip()
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return

        if text.startswith(("/start", "/help")):
            await self.api.send_message(
                chat_id,
                "أرسل رابطًا يحتوي على فيديو، ثم اختر فيديو أو صوت والجودة المناسبة.",
            )
            return
        if not text.startswith(("https://", "http://")):
            await self.api.send_message(chat_id, "⚠️ أرسل رابطًا صالحًا يحتوي على فيديو.")
            return

        await self._handle_url(chat_id=chat_id, owner_id=user_id, url=text)

    def _is_duplicate(self, update_id: int) -> bool:
        if update_id in self._seen_updates:
            return True
        if len(self._seen_order) == self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self._seen_updates.discard(oldest)
        self._seen_order.append(update_id)
        self._seen_updates.add(update_id)
        return False

    async def _handle_url(self, *, chat_id: int, owner_id: int, url: str) -> None:
        from .security import validate_public_url

        try:
            url = validate_public_url(url)
        except ValueError as exc:
            await self.api.send_message(chat_id, f"⚠️ {str(exc)[:180]}")
            return

        token = self.selections.create(
            owner_id=owner_id,
            value={"url": url, "title": "", "qualities": [], "thumbnail": ""},
        )
        await self.api.send_message(
            chat_id,
            "🎯 اختر نوع التحميل:",
            reply_markup=kind_keyboard(token),
        )

    async def _inspect_with_progress(
        self,
        chat_id: int,
        message_id: int,
        url: str,
    ) -> dict[str, Any]:
        task = asyncio.create_task(asyncio.to_thread(self._inspect_sync, url))
        step = 0
        while not task.done():
            await asyncio.sleep(1.2)
            if step < 90:
                step += 10
                await self._safe_edit(chat_id, message_id, progress_text("inspecting", step))
        result = await task
        await self._safe_edit(chat_id, message_id, progress_text("inspecting", 100))
        return result

    def _inspect_sync(self, url: str) -> dict[str, Any]:
        from .extractor import UniversalExtractor

        extractor = UniversalExtractor(
            use_browser=bool(self.settings.enable_browser_capture),
            browser_bin=self.settings.browser_bin,
            request_timeout=self.settings.request_timeout_seconds,
            max_filesize=self.settings.max_file_size_bytes,
        )
        try:
            return extractor.inspect(url)
        finally:
            extractor.cleanup()

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        parsed = parse_callback(str(callback.get("data") or ""))
        if parsed is None:
            return
        callback_id = str(callback.get("id") or "")
        owner_id = (callback.get("from") or {}).get("id")
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        if not isinstance(owner_id, int) or not isinstance(chat_id, int) or not isinstance(message_id, int):
            return

        action, token, quality_key = parsed
        session = self.selections.get(token, owner_id)
        if session is None:
            await self.api.answer_callback_query(
                callback_id,
                "❌ انتهت صلاحية الاختيار أو أنه ليس لك.",
                show_alert=True,
            )
            return
        await self.api.answer_callback_query(callback_id)

        if action == "video":
            qualities = [_best_quality()]
            try:
                inspected = await self._inspect_with_progress(
                    chat_id,
                    message_id,
                    str(session["url"]),
                )
                real_qualities = [
                    option
                    for option in normalize_qualities(inspected.get("formats") or [])
                    if option.key not in {"best", "original"}
                ]
                qualities.extend(reversed(real_qualities))
                session["title"] = str(inspected.get("title") or session.get("title") or "")
                session["thumbnail"] = str(
                    inspected.get("thumbnail") or session.get("thumbnail") or ""
                )
            except Exception:  # noqa: BLE001
                await self._safe_edit(
                    chat_id,
                    message_id,
                    "⚠️ تعذر فحص الجودات؛ سيظهر خيار الأفضل فقط.",
                )
            session["qualities"] = qualities
            await self.api.edit_message_reply_markup(
                chat_id,
                message_id,
                quality_keyboard(token, qualities),
            )
            return

        if action == "audio":
            self.selections.pop(token, owner_id)
            await self._safe_delete(chat_id, message_id)
            await self._enqueue(
                chat_id=chat_id,
                owner_id=owner_id,
                url=str(session["url"]),
                title=str(session["title"]),
                kind="audio",
                selector="audio",
            )
            return

        qualities: list[QualityOption] = list(session.get("qualities") or [])
        selected = next((item for item in qualities if item.key == quality_key), None)
        if selected is None:
            await self.api.answer_callback_query(
                callback_id,
                "❌ هذه الجودة لم تعد متاحة.",
                show_alert=True,
            )
            return
        self.selections.pop(token, owner_id)
        await self._safe_delete(chat_id, message_id)
        await self._enqueue(
            chat_id=chat_id,
            owner_id=owner_id,
            url=str(session["url"]),
            title=str(session["title"]),
            kind="video",
            selector=selected.selector,
        )

    async def _enqueue(
        self,
        *,
        chat_id: int,
        owner_id: int,
        url: str,
        title: str,
        kind: str,
        selector: str,
    ) -> None:
        position = self.queue.qsize() + (1 if self._active else 0) + 1
        if position == 1:
            text = progress_text("downloading", 0)
        else:
            text = f"📥 تمت إضافة الرابط إلى الطابور — موضعك #{position}"
        status = await self.api.send_message(chat_id, text)
        request = DownloadRequest(
            chat_id=chat_id,
            owner_id=owner_id,
            url=url,
            title=title,
            kind=kind,
            selector=selector,
            status_message_id=int(status.get("message_id") or 0),
        )
        await self.queue.put(request)
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        while True:
            request = await self.queue.get()
            self._active = True
            try:
                await self._process_request(request)
            finally:
                self._active = False
                self.queue.task_done()

    async def _process_request(self, request: DownloadRequest) -> None:
        root = Path(tempfile.mkdtemp(prefix="yjdownload-", dir=self.settings.storage_path))
        state = _ProgressState()
        try:
            await self._safe_edit(
                request.chat_id,
                request.status_message_id,
                progress_text("downloading", 0),
            )
            task = asyncio.create_task(
                asyncio.to_thread(self._download_sync, request, root, state)
            )
            last_stage = ""
            last_step = -10
            preparing_estimate = 0
            while not task.done():
                stage, pct = state.get()
                if stage == "preparing" and pct < 100:
                    preparing_estimate = min(90, max(preparing_estimate + 10, pct))
                    pct = preparing_estimate
                step = progress_step(pct)
                if stage != last_stage or step >= last_step + 10:
                    await self._safe_edit(
                        request.chat_id,
                        request.status_message_id,
                        progress_text(stage, step),
                    )
                    last_stage, last_step = stage, step
                await asyncio.sleep(1)
            result = await task

            path = Path(result["path"])
            if path.stat().st_size > int(self.settings.telegram_send_limit_bytes):
                raise RuntimeError("حجم الملف يتجاوز حد تيليجرام البالغ 2GB")

            upload_state = _ProgressState("uploading", 0)

            def upload_progress(done: int, total: int) -> None:
                pct = int(done * 100 / total) if total else 0
                upload_state.set("uploading", pct)

            await self._safe_edit(
                request.chat_id,
                request.status_message_id,
                progress_text("uploading", 0),
            )
            if request.kind == "audio":
                send_task = asyncio.create_task(
                    self.api.send_audio_file(
                        request.chat_id,
                        path,
                        title=str(result["title"]),
                        progress=upload_progress,
                    )
                )
            else:
                send_task = asyncio.create_task(
                    self.api.send_video_file(
                        request.chat_id,
                        path,
                        progress=upload_progress,
                    )
                )
            last_step = -10
            while not send_task.done():
                _, pct = upload_state.get()
                step = progress_step(pct)
                if step >= last_step + 10:
                    await self._safe_edit(
                        request.chat_id,
                        request.status_message_id,
                        progress_text("uploading", step),
                    )
                    last_step = step
                await asyncio.sleep(0.75)
            await send_task
            await self._safe_delete(request.chat_id, request.status_message_id)
        except Exception as exc:  # noqa: BLE001
            await self._safe_edit(
                request.chat_id,
                request.status_message_id,
                f"❌ فشلت العملية: {self._safe_error(exc)}",
            )
        finally:
            cleanup_path(root)

    def _download_sync(
        self,
        request: DownloadRequest,
        root: Path,
        state: _ProgressState,
    ) -> dict[str, Any]:
        from .extractor import UniversalExtractor

        def hook(data: dict[str, Any]) -> None:
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes") or 0
                pct = int(done * 100 / total) if total else 0
                state.set("downloading", pct)
            elif status in {"finished", "post-processing"}:
                state.set("preparing", 0)

        extractor = UniversalExtractor(
            use_browser=bool(self.settings.enable_browser_capture),
            browser_bin=self.settings.browser_bin,
            request_timeout=self.settings.request_timeout_seconds,
            progress_hook=hook,
            max_filesize=self.settings.max_file_size_bytes,
            max_duration=self.settings.max_duration_seconds,
        )
        try:
            result = extractor.download(
                request.url,
                root,
                preferred=request.selector,
                title_hint=request.title,
            )
            source = Path(result["path"])
            title = str(result.get("title") or request.title or "مقطع")
            duration = result.get("duration")
            if request.kind == "audio":
                state.set("converting", 0)
                output = root / safe_media_title(title, ".mp3")
                self._convert_audio(source, output, duration, state)
                if source != output:
                    source.unlink(missing_ok=True)
                return {"path": str(output), "title": title}

            state.set("preparing", 100)
            output = root / safe_media_title(title, ".mp4")
            if source.resolve() != output.resolve():
                if source.suffix.lower() == ".mp4":
                    source.replace(output)
                else:
                    self._remux_video(source, output, duration, state)
                    source.unlink(missing_ok=True)
            return {"path": str(output), "title": title}
        finally:
            extractor.cleanup()

    def _convert_audio(
        self,
        source: Path,
        output: Path,
        duration: float | int | None,
        state: _ProgressState,
    ) -> None:
        from .config import ffmpeg_location

        command = [
            ffmpeg_location(),
            "-y",
            "-i",
            str(source),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            "-map_metadata",
            "0",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ]
        self._run_ffmpeg_progress(command, duration, "converting", state)

    def _remux_video(
        self,
        source: Path,
        output: Path,
        duration: float | int | None,
        state: _ProgressState,
    ) -> None:
        from .config import ffmpeg_location

        state.set("preparing", 0)
        command = [
            ffmpeg_location(),
            "-y",
            "-i",
            str(source),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ]
        self._run_ffmpeg_progress(command, duration, "preparing", state)

    @staticmethod
    def _run_ffmpeg_progress(
        command: list[str],
        duration: float | int | None,
        stage: str,
        state: _ProgressState,
    ) -> None:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        total = float(duration or 0)
        assert process.stdout is not None
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key in {"out_time_us", "out_time_ms"} and total > 0:
                try:
                    seconds = int(value) / 1_000_000
                    state.set(stage, int(seconds * 100 / total))
                except ValueError:
                    pass
        code = process.wait(timeout=600)
        if code != 0:
            raise RuntimeError("فشل FFmpeg في تجهيز الملف")
        state.set(stage, 100)

    async def _safe_edit(self, chat_id: int, message_id: int, text: str) -> None:
        if not message_id:
            return
        try:
            await self.api.edit_message_text(chat_id, message_id, text)
        except Exception:  # noqa: BLE001
            pass

    async def _safe_delete(self, chat_id: int, message_id: int) -> None:
        if not message_id:
            return
        try:
            await self.api.delete_message(chat_id, message_id)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        lowered = text.lower()
        if "drm" in lowered:
            return "المقطع محمي بتشفير DRM ولا يمكن تنزيله."
        if any(value in lowered for value in ("login", "cookie", "forbidden", "403")):
            return f"الموقع يطلب تسجيل دخول أو يمنع التحميل من الخادم. ({text[:120]})"
        return text[:220]


_default_flow: TelegramFlow | None = None


def get_telegram_flow() -> TelegramFlow:
    global _default_flow
    if _default_flow is None:
        from .config import settings

        base_url = settings.telegram_bot_api_base or "https://api.telegram.org"
        _default_flow = TelegramFlow(
            api=TelegramAPI(
                token=settings.telegram_bot_token,
                base_url=base_url,
                timeout_seconds=600,
            ),
            settings=settings,
        )
    return _default_flow
