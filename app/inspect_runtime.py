from __future__ import annotations

import multiprocessing as mp
from queue import Empty
from typing import Any

from .config import cookie_file_for_url, settings
from .redaction import sanitize_external_error


def _youtube_proxy(url: str) -> str:
    from .pot_runtime_patch import _is_youtube_url

    if not _is_youtube_url(url):
        return ""
    return str(settings.youtube_proxy_url or "").strip()


def _safe_error(exc: BaseException) -> str:
    return sanitize_external_error(
        exc,
        secrets_to_hide=(
            settings.telegram_bot_token,
            settings.telegram_api_hash,
            settings.worker_shared_secret,
            settings.admin_api_token,
            settings.youtube_proxy_url,
        ),
        limit=500,
    )


def _inspect_in_child(url: str, result_queue: Any) -> None:
    # Install runtime patches inside the child. Inspection deliberately avoids
    # browser capture: Chromium belongs to bounded download jobs, not HTTP workers.
    from . import pot_runtime_patch as _pot_runtime_patch  # noqa: F401
    from . import network_runtime_patch as _network_runtime_patch  # noqa: F401
    from .extractor import UniversalExtractor

    extractor = UniversalExtractor(
        cookies_file=cookie_file_for_url(url),
        proxy_url=_youtube_proxy(url),
        use_browser=False,
        browser_bin=settings.browser_bin,
        request_timeout=min(30, max(10, int(settings.request_timeout_seconds))),
        max_filesize=settings.max_file_size_bytes,
    )
    try:
        result_queue.put({"ok": True, "result": extractor.inspect(url)})
    except BaseException as exc:  # noqa: BLE001
        result_queue.put({"ok": False, "error": _safe_error(exc)})
    finally:
        extractor.cleanup()


def inspect_with_timeout(url: str, *, timeout_seconds: int = 35) -> dict[str, Any]:
    timeout = max(1, min(60, int(timeout_seconds)))
    context = mp.get_context("fork")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_inspect_in_child,
        args=(str(url), result_queue),
        daemon=False,
    )
    process.start()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(2)
        raise TimeoutError(f"انتهت مهلة فحص الرابط بعد {timeout} ثانية")

    try:
        payload = result_queue.get(timeout=2)
    except Empty as exc:
        raise RuntimeError(f"تعطلت عملية فحص الرابط برمز {process.exitcode}") from exc
    finally:
        result_queue.close()
        result_queue.join_thread()

    if not isinstance(payload, dict):
        raise RuntimeError("تعذر قراءة نتيجة فحص الرابط")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "فشل فحص الرابط"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("نتيجة فحص الرابط غير صالحة")
    return result
