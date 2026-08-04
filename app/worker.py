from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from .config import settings
from .db import Job, SessionLocal, utcnow
from .extractor import UniversalExtractor
from .redaction import sanitize_external_error
from .security import make_token
from .status import write_status


def _owner_cookies(owner_key: str) -> str | None:
    """Return a per-user cookie file when one exists."""
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


def process_job(job_id: str) -> None:
    with SessionLocal() as database:
        job = database.get(Job, job_id)
        if not job:
            return
        job.status = "downloading"
        database.commit()
        owner_key = job.owner_key or ""

    outdir = settings.storage_path / job_id
    outdir.mkdir(exist_ok=True)
    write_status(job_id, "downloading", 0)

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
        result = extractor.download(
            job.url,
            outdir,
            preferred=job.requested_format or "best",
        )
        media_file = Path(result["path"])
        if not media_file.exists():
            raise ValueError("لم يتم إنشاء الملف")
        write_status(job_id, "done", 100, size=media_file.stat().st_size)
        with SessionLocal() as database:
            current = database.get(Job, job_id)
            if current is None:
                return
            current.title = result.get("title") or current.title
            current.thumbnail = result.get("thumbnail")
            current.duration = result.get("duration")
            current.file_size = media_file.stat().st_size
            current.file_path = str(media_file)
            current.status = "ready"
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
    finally:
        extractor.cleanup()


def main() -> None:
    import redis

    client = redis.Redis.from_url(settings.redis_url)
    while True:
        item = client.blpop("yjdownload:jobs", timeout=5)
        if item:
            process_job(item[1].decode())


if __name__ == "__main__":
    main()
