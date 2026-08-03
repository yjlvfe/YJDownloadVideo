from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .config import settings
from .job_queue import DurableSelectionStore, JobQueue, QueueJob
from .media_choice import QualityOption, normalize_qualities, quality_options


_QUEUE: JobQueue | None = None
_PATCHED = False


def get_job_queue() -> JobQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = JobQueue(settings.db_path)
    return _QUEUE


def actual_video_qualities(raw_formats: list[dict[str, Any]] | None) -> list[QualityOption]:
    """Return truthful buttons: best plus only heights reported by inspection."""
    best = quality_options()[0]
    dynamic = [item for item in normalize_qualities(raw_formats) if item.key != "original"]
    dynamic.sort(key=lambda item: int(item.key), reverse=True)
    return [best, *dynamic]


def quality_payloads(qualities: list[QualityOption]) -> list[dict[str, Any]]:
    return [
        {
            "key": item.key,
            "label": item.label,
            "selector": item.selector,
            "size": item.size,
        }
        for item in qualities
    ]


def qualities_from_payloads(payloads: Any) -> list[QualityOption]:
    result: list[QualityOption] = []
    for value in payloads if isinstance(payloads, list) else []:
        if not isinstance(value, dict):
            continue
        try:
            result.append(
                QualityOption(
                    key=str(value["key"]),
                    label=str(value["label"]),
                    selector=str(value["selector"]),
                    size=int(value["size"]) if value.get("size") is not None else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


def active_media_jobs_for_owner(queue: JobQueue, owner_id: int) -> int:
    with queue._connect() as connection:
        rows = connection.execute(
            """
            SELECT payload_json FROM durable_jobs
            WHERE kind = 'media_download'
              AND state IN ('queued', 'claimed', 'retry_wait')
            """
        ).fetchall()
    count = 0
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("owner_id") == int(owner_id):
            count += 1
    return count


def is_retryable_job_error(message: str) -> bool:
    lowered = str(message or "").lower()
    terminal_markers = (
        "youtube_",
        "drm",
        "تشفير",
        "يتجاوز الحد",
        "غير متاحة",
        "غير مدعوم",
        "invalid worker signature",
        "authentication required",
    )
    return not any(marker in lowered for marker in terminal_markers)


class RecoverableSelectionStore(DurableSelectionStore):
    """Selection store whose value remains until the media job is durable."""

    def pop(self, token: str, owner_id: int) -> dict[str, Any] | None:
        return self.get(token, owner_id)

    def delete(self, token: str, owner_id: int) -> None:
        with self.queue._connect() as connection:
            connection.execute(
                "DELETE FROM telegram_selections WHERE token = ? AND owner_id = ?",
                (str(token), int(owner_id)),
            )


def install_durable_flow_patches(queue: JobQueue | None = None) -> None:
    global _PATCHED
    if _PATCHED:
        return

    from . import telegram_flow

    queue = queue or get_job_queue()
    original_handle_update = telegram_flow.TelegramFlow.handle_update
    original_process_request = telegram_flow.TelegramFlow._process_request

    def preserve_runtime_storage(self: Any) -> None:
        Path(self.settings.storage_path).mkdir(parents=True, exist_ok=True)

    def selection_factory(ttl_seconds: int = 15 * 60, **_: Any) -> RecoverableSelectionStore:
        return RecoverableSelectionStore(
            queue.database_path,
            ttl_seconds=ttl_seconds,
        )

    async def handle_update(self: Any, data: dict[str, Any]) -> None:
        # Durable queue owns update-id dedupe. Remove the in-memory dedupe input so
        # a recovered lease cannot be silently skipped in the same process.
        self._durable_update_id = data.get("update_id")
        replay_safe_data = dict(data)
        replay_safe_data.pop("update_id", None)
        try:
            await original_handle_update(self, replay_safe_data)
        finally:
            self._durable_update_id = None

    async def handle_callback(self: Any, callback: dict[str, Any]) -> None:
        parsed = telegram_flow.parse_callback(str(callback.get("data") or ""))
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
            try:
                await self._safe_edit(
                    chat_id,
                    message_id,
                    telegram_flow.progress_text("inspecting", 0),
                )
                inspected = await self._inspect_with_progress(
                    chat_id,
                    message_id,
                    str(session["url"]),
                )
                qualities = actual_video_qualities(inspected.get("formats"))
                replacement_token = self.selections.create(
                    owner_id=owner_id,
                    value={
                        "url": str(session["url"]),
                        "title": str(inspected.get("title") or session.get("title") or ""),
                        "thumbnail": str(inspected.get("thumbnail") or ""),
                        "qualities": quality_payloads(qualities),
                    },
                )
                if hasattr(self.selections, "delete"):
                    self.selections.delete(token, owner_id)
                await self.api.edit_message_text(
                    chat_id,
                    message_id,
                    "🎯 اختر الجودة المتاحة:",
                    reply_markup=telegram_flow.quality_keyboard(replacement_token, qualities),
                )
            except Exception as exc:  # noqa: BLE001
                await self._safe_edit(
                    chat_id,
                    message_id,
                    f"❌ تعذر فحص الجودات: {self._safe_error(exc)}",
                )
            return

        self._durable_selection_token = token
        try:
            if action == "audio":
                self.selections.pop(token, owner_id)
                await self._safe_delete(chat_id, message_id)
                await self._enqueue(
                    chat_id=chat_id,
                    owner_id=owner_id,
                    url=str(session["url"]),
                    title=str(session.get("title") or ""),
                    kind="audio",
                    selector="audio",
                )
                return

            qualities = qualities_from_payloads(session.get("qualities"))
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
                title=str(session.get("title") or ""),
                kind="video",
                selector=selected.selector,
            )
        finally:
            self._durable_selection_token = None

    async def enqueue_media(
        self: Any,
        *,
        chat_id: int,
        owner_id: int,
        url: str,
        title: str,
        kind: str,
        selector: str,
    ) -> None:
        current = await asyncio.to_thread(active_media_jobs_for_owner, queue, owner_id)
        if current >= int(self.settings.max_active_jobs_per_telegram_user):
            await self.api.send_message(
                chat_id,
                "⚠️ لديك عمليات نشطة كثيرة. انتظر انتهاء إحداها ثم أعد المحاولة.",
            )
            return

        token = str(getattr(self, "_durable_selection_token", "") or "")
        update_id = getattr(self, "_durable_update_id", None)
        material = f"{token}\0{update_id}\0{kind}\0{selector}\0{url}"
        idempotency_key = hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
        _, created = await asyncio.to_thread(
            queue.enqueue,
            idempotency_key=idempotency_key,
            kind="media_download",
            payload={
                "chat_id": int(chat_id),
                "owner_id": int(owner_id),
                "url": str(url),
                "title": str(title),
                "kind": str(kind),
                "selector": str(selector),
            },
        )
        if token and created and hasattr(self.selections, "delete"):
            self.selections.delete(token, owner_id)
        if not created:
            await self.api.send_message(chat_id, "ℹ️ هذا الطلب موجود بالفعل في طابور التحميل.")

    async def process_request(self: Any, request: Any) -> None:
        # The original UI method reports failures to Telegram and swallows them.
        # Record that message and re-raise after cleanup so the durable queue can
        # mark retry_wait/failed instead of falsely recording completion.
        failure_messages: list[str] = []
        original_safe_edit = self._safe_edit

        async def recording_edit(chat_id: int, message_id: int, text: str) -> None:
            normalized = str(text or "")
            prefix = "❌ فشلت العملية:"
            if normalized.startswith(prefix):
                failure_messages.append(normalized[len(prefix):].strip())
            await original_safe_edit(chat_id, message_id, text)

        self._safe_edit = recording_edit
        try:
            await original_process_request(self, request)
        finally:
            self._safe_edit = original_safe_edit
        if failure_messages:
            raise RuntimeError(failure_messages[-1])

    telegram_flow.SelectionStore = selection_factory
    telegram_flow.TelegramFlow.clear_runtime_storage = preserve_runtime_storage
    telegram_flow.TelegramFlow.handle_update = handle_update
    telegram_flow.TelegramFlow._handle_callback = handle_callback
    telegram_flow.TelegramFlow._enqueue = enqueue_media
    telegram_flow.TelegramFlow._process_request = process_request
    _PATCHED = True


async def _lease_heartbeat(
    queue: JobQueue,
    job_id: str,
    worker_id: str,
    *,
    lease_seconds: int,
) -> None:
    interval = max(5, lease_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        ok = await asyncio.to_thread(
            queue.heartbeat,
            job_id,
            worker_id,
            lease_seconds=lease_seconds,
        )
        if not ok:
            return


async def _dispatch(job: QueueJob) -> None:
    if job.kind == "web_download":
        from .db import Job, SessionLocal
        from .worker import process_job

        await asyncio.to_thread(process_job, str(job.payload["job_id"]))
        with SessionLocal() as database:
            web_job = database.get(Job, str(job.payload["job_id"]))
            if web_job is None:
                raise RuntimeError("web job disappeared")
            if web_job.status == "failed":
                raise RuntimeError(str(web_job.error or "web media job failed"))
        return

    from .telegram_flow import DownloadRequest, get_telegram_flow, progress_text

    flow = get_telegram_flow()
    if job.kind == "telegram_update":
        await flow.handle_update(job.payload)
        return
    if job.kind != "media_download":
        raise RuntimeError("unsupported durable job kind")

    payload = job.payload
    chat_id = int(payload["chat_id"])
    status = await flow.api.send_message(chat_id, progress_text("downloading", 0))
    request = DownloadRequest(
        chat_id=chat_id,
        owner_id=int(payload["owner_id"]),
        url=str(payload["url"]),
        title=str(payload.get("title") or ""),
        kind=str(payload["kind"]),
        selector=str(payload["selector"]),
        status_message_id=int(status.get("message_id") or 0),
    )
    await flow._process_request(request)


async def run_durable_worker(
    *,
    queue: JobQueue | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 120,
) -> None:
    queue = queue or get_job_queue()
    install_durable_flow_patches(queue)
    worker_id = worker_id or f"{os.getenv('HOSTNAME', 'render')}:{os.getpid()}"

    while True:
        job = await asyncio.to_thread(
            queue.claim,
            worker_id,
            lease_seconds=lease_seconds,
        )
        if job is None:
            await asyncio.sleep(0.5)
            continue

        heartbeat = asyncio.create_task(
            _lease_heartbeat(
                queue,
                job.id,
                worker_id,
                lease_seconds=lease_seconds,
            )
        )
        try:
            await _dispatch(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            if is_retryable_job_error(message) and job.attempt < 3:
                retry_at = time.time() + min(300, 20 * (2 ** max(0, job.attempt - 1)))
                await asyncio.to_thread(
                    queue.fail,
                    job.id,
                    message,
                    retry_at=retry_at,
                    worker_id=worker_id,
                )
            else:
                await asyncio.to_thread(
                    queue.fail,
                    job.id,
                    message,
                    worker_id=worker_id,
                )
        else:
            await asyncio.to_thread(
                queue.complete,
                job.id,
                worker_id=worker_id,
            )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass


def start_durable_worker() -> asyncio.Task[None] | None:
    if settings.vercel_mode:
        return None
    install_durable_flow_patches(get_job_queue())
    return asyncio.create_task(run_durable_worker())
