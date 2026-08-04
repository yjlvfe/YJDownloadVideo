from __future__ import annotations

import multiprocessing as mp
from datetime import timedelta
from pathlib import Path
from queue import Empty
from typing import Any

from .config import settings
from .db import Job, SessionLocal, utcnow
from .redaction import sanitize_external_error
from .security import make_token
from .status import write_status


def _owner_cookies(owner_key: str) -> str | None:
    path = settings.cookies_path / f"{owner_key}.txt"
    return str(path) if path.is_file() else None


def _safe_failure(exc: BaseException) -> str:
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


def _extract_in_child(job_id: str, result_queue: Any) -> None:
    from .extractor import UniversalExtractor

    with SessionLocal() as database:
        job = database.get(Job, job_id)
        if job is None:
            result_queue.put({"ok": False, "error": "web job disappeared"})
            return
        owner_key = job.owner_key or ""
        url = str(job.url)
        requested_format = str(job.requested_format or "best")

    outdir = settings.storage_path / job_id
    outdir.mkdir(exist_ok=True)

    def hook(data: dict) -> None:
        if data.get("status") == "downloading":
            with SessionLocal() as database:
                current = database.get(Job, job_id)
                if current:
                    current.status = "downloading"
                    database.commit()
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            percent = int(done / total * 100) if total else 0
            write_status(job_id, "downloading", percent, size=done)
        elif data.get("status") in {"finished", "post-processing"}:
            write_status(job_id, "preparing", 100)

    extractor = UniversalExtractor(
        cookies_file=_owner_cookies(owner_key),
        use_browser=settings.enable_browser_capture and not settings.vercel_mode,
        progress_hook=hook,
        max_filesize=settings.max_file_size_bytes,
        max_duration=settings.max_duration_seconds,
        browser_bin=settings.browser_bin,
        request_timeout=settings.request_timeout_seconds,
    )
    try:
        result = extractor.download(url, outdir, preferred=requested_format)
        media_file = Path(result["path"])
        if not media_file.exists():
            raise ValueError("لم يتم إنشاء الملف")
        result_queue.put(
            {
                "ok": True,
                "result": result,
                "file_path": str(media_file),
                "file_size": media_file.stat().st_size,
            }
        )
    except BaseException as exc:  # noqa: BLE001
        result_queue.put({"ok": False, "error": _safe_failure(exc)})
    finally:
        extractor.cleanup()


def _run_extraction_with_timeout(job_id: str) -> dict[str, Any]:
    context = mp.get_context("fork")
    result_queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_extract_in_child,
        args=(job_id, result_queue),
        daemon=False,
    )
    process.start()
    timeout_seconds = max(90, min(300, int(settings.request_timeout_seconds) * 4))
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(2)
        raise TimeoutError(f"انتهت مهلة التحميل بعد {timeout_seconds} ثانية")
    try:
        payload = result_queue.get(timeout=2)
    except Empty as exc:
        raise RuntimeError(f"تعطلت عملية الاستخراج برمز {process.exitcode}") from exc
    finally:
        result_queue.close()
        result_queue.join_thread()
    if not isinstance(payload, dict):
        raise RuntimeError("تعذر قراءة نتيجة الاستخراج")
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "فشل استخراج الوسائط"))
    return payload


def process_job(job_id: str) -> None:
    with SessionLocal() as database:
        job = database.get(Job, job_id)
        if not job:
            return
        job.status = "downloading"
        job.error = None
        database.commit()

    write_status(job_id, "downloading", 0)
    try:
        payload = _run_extraction_with_timeout(job_id)
        result = dict(payload["result"])
        media_file = Path(str(payload["file_path"]))
        file_size = int(payload["file_size"])
        write_status(job_id, "done", 100, size=file_size)
        with SessionLocal() as database:
            current = database.get(Job, job_id)
            if current is None:
                return
            current.title = result.get("title") or current.title
            current.thumbnail = result.get("thumbnail")
            current.duration = result.get("duration")
            current.file_size = file_size
            current.file_path = str(media_file)
            current.status = "ready"
            current.error = None
            current.download_token = make_token(job_id)
            current.expires_at = utcnow() + timedelta(seconds=settings.file_ttl_seconds)
            database.commit()
    except Exception as exc:  # noqa: BLE001
        safe = _safe_failure(exc)
        write_status(job_id, "failed", message=safe[:300])
        with SessionLocal() as database:
            current = database.get(Job, job_id)
            if current:
                current.status = "failed"
                current.error = safe
                database.commit()


def main() -> None:
    import redis

    client = redis.Redis.from_url(settings.redis_url)
    while True:
        item = client.blpop("yjdownload:jobs", timeout=5)
        if item:
            process_job(item[1].decode())


if __name__ == "__main__":
    main()
