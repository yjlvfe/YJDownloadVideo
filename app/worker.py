import os, sys, time, subprocess
from datetime import timedelta
from pathlib import Path
from .config import settings
from .db import SessionLocal, Job, utcnow
from .security import make_token
from .extractor import UniversalExtractor
from .status import write_status

def _owner_cookies(owner_key: str) -> str | None:
    """ملف كوكيز خاص بالمستخدم إن وُجد (من البوت: data/cookies/<owner_key>.txt)."""
    p = settings.cookies_path / f"{owner_key}.txt"
    return str(p) if p.is_file() else None

def process_job(job_id):
    with SessionLocal() as db:
        job=db.get(Job,job_id)
        if not job: return
        job.status='downloading'; db.commit()
        owner_key = job.owner_key or ""
    outdir=settings.storage_path / job_id; outdir.mkdir(exist_ok=True)
    write_status(job_id, "downloading", 0)
    def hook(d):
        if d['status']=='downloading':
            with SessionLocal() as db:
                j=db.get(Job,job_id)
                if j: j.status='downloading'; db.commit()
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            done = d.get('downloaded_bytes') or 0
            pct = int(done / total * 100) if total else 0
            write_status(job_id, "downloading", pct, size=done)
        elif d['status'] in ('finished', 'post-processing'):
            write_status(job_id, "preparing", 100)
    ex = UniversalExtractor(
        cookies_file=_owner_cookies(owner_key),
        use_browser=settings.enable_browser_capture and not settings.vercel_mode,
        progress_hook=hook,
        max_filesize=settings.max_file_size_bytes,
        max_duration=settings.max_duration_seconds,
        browser_bin=settings.browser_bin,
        request_timeout=settings.request_timeout_seconds,
    )
    try:
        res = ex.download(job.url, outdir, preferred=job.requested_format or 'best')
        file=Path(res['path'])
        if not file.exists(): raise ValueError('لم يتم إنشاء الملف')
        write_status(job_id, "done", 100, size=file.stat().st_size)
        with SessionLocal() as db:
            j=db.get(Job,job_id)
            j.title=res.get('title') or j.title
            j.thumbnail=res.get('thumbnail')
            j.duration=res.get('duration')
            j.file_size=file.stat().st_size
            j.file_path=str(file)
            j.status='ready'
            j.download_token=make_token(job_id)
            j.expires_at=utcnow()+timedelta(seconds=settings.file_ttl_seconds)
            db.commit()
    except Exception as e:
        write_status(job_id, "failed", message=str(e)[:300])
        with SessionLocal() as db:
            j=db.get(Job,job_id)
            if j: j.status='failed'; j.error=str(e)[:500]; db.commit()
    finally:
        ex.cleanup()

def main():
    import redis
    r=redis.Redis.from_url(settings.redis_url)
    while True:
        item=r.blpop('yjdownload:jobs',timeout=5)
        if item: process_job(item[1].decode())
if __name__=='__main__': main()
