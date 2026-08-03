from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any

from .config import settings
from .job_queue import DurableSelectionStore, JobQueue, QueueJob


_QUEUE: JobQueue | None = None
_PATCHED = False


def get_job_queue() -> JobQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = JobQueue(settings.db_path)
    return _QUEUE


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
    original_handle_callback = telegram_flow.TelegramFlow._handle_callback

    def selection_factory(ttl_seconds: int = 15 * 60, **_: Any) -> RecoverableSelectionStore:
        return RecoverableSelectionStore(
            queue.database_path,
            ttl_seconds=ttl_seconds,
        )

    async def handle_update(self: Any, data: dict[str, Any]) -> None:
        self._durable_update_id = data.get("update_id")
        try:
            await original_handle_update(self, data)
        finally:
            self._durable_update_id = None

    async def handle_callback(self: Any, callback: dict[str, Any]) -> None:
        parsed = telegram_flow.parse_callback(str(callback.get("data") or ""))
        self._durable_selection_token = parsed[1] if parsed else None
        try:
            await original_handle_callback(self, callback)
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

    telegram_flow.SelectionStore = selection_factory
    telegram_flow.TelegramFlow.handle_update = handle_update
    telegram_flow.TelegramFlow._handle_callback = handle_callback
    telegram_flow.TelegramFlow._enqueue = enqueue_media
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
            if job.attempt < 3:
                retry_at = time.time() + min(300, 20 * (2 ** max(0, job.attempt - 1)))
                await asyncio.to_thread(
                    queue.fail,
                    job.id,
                    exc.__class__.__name__,
                    retry_at=retry_at,
                    worker_id=worker_id,
                )
            else:
                await asyncio.to_thread(
                    queue.fail,
                    job.id,
                    exc.__class__.__name__,
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
