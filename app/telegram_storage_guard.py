from __future__ import annotations

import threading
from pathlib import Path

from .storage_leases import StorageLeaseRegistry
from .telegram_flow import DownloadRequest, TelegramFlow, _ProgressState


_original_process_request = TelegramFlow._process_request
_installed = False


def _refresh_until_stopped(lease, stop: threading.Event) -> None:
    while not stop.wait(30):
        try:
            lease.heartbeat()
        except (OSError, RuntimeError):
            return


async def process_request_with_storage_guard(
    flow: TelegramFlow,
    request: DownloadRequest,
) -> None:
    original_download = flow._download_sync
    leases = []

    def download_with_lease(
        current_request: DownloadRequest,
        root: Path,
        state: _ProgressState,
    ):
        lease = StorageLeaseRegistry(flow.settings.storage_path).lease(root)
        stop = threading.Event()
        thread = threading.Thread(
            target=_refresh_until_stopped,
            args=(lease, stop),
            daemon=True,
            name=f"telegram-storage-{root.name}",
        )
        leases.append((lease, stop, thread))
        thread.start()
        return original_download(current_request, root, state)

    flow._download_sync = download_with_lease
    try:
        await _original_process_request(flow, request)
    finally:
        flow._download_sync = original_download
        for lease, stop, thread in leases:
            stop.set()
            thread.join(timeout=2)
            lease.close()


def install() -> None:
    global _installed
    if _installed:
        return
    TelegramFlow._process_request = process_request_with_storage_guard
    _installed = True
