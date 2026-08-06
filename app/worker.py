import os, sys, time, shutil, subprocess
from concurrent.futures import ThreadPoolExecutor
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
        job = db.get(Job, job_id)
        if not job:
            return
        url = job.url
        fmt = job.requested_format or 'best'
        owner_key = job.owner_key or ""
        job.status = 'downloading'
        db.commit()
    outdir = settings.storage_path / job_id
    outdir.mkdir(exist_ok=True)
    write_status(job_id, "downloading", 0)

    # التقدم يُكتب في status.json فقط — لا مساس بـ SQLite عند كل نبضة
    # (كان commit لكل حدث تقدم ~1/ثانية = تنافس قفل بلا أي فائدة)
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            done = d.get('downloaded_bytes') or 0
            pct = int(done / total * 100) if total else 0
            write_status(job_id, "downloading", pct, size=done)
        elif d['status'] in ('finished', 'post-processing'):
            write_status(job_id, "preparing", 100)

    ex = UniversalExtractor(
        cookies_file=_owner_cookies(owner_key),
        progress_hook=hook,
        max_filesize=settings.max_file_size_bytes,
        max_duration=settings.max_duration_seconds,
        browser_bin=settings.browser_bin,
        request_timeout=settings.request_timeout_seconds,
    )
    try:
        res = ex.download(url, outdir, preferred=fmt)
        file = Path(res['path'])
        if not file.exists():
            raise ValueError('لم يتم إنشاء الملف')
        write_status(job_id, "done", 100, size=file.stat().st_size)
        with SessionLocal() as db:
            j = db.get(Job, job_id)
            if not j:
                return
            j.title = res.get('title') or j.title
            j.thumbnail = res.get('thumbnail')
            j.duration = res.get('duration')
            j.file_size = file.stat().st_size
            j.file_path = str(file)
            j.status = 'ready'
            j.download_token = make_token(job_id)
            j.expires_at = utcnow() + timedelta(seconds=settings.file_ttl_seconds)
            db.commit()
    except Exception as e:
        # 🗑️ فشل نظيف: لا بقايا على القرص + انتهاء صلاحية ليلتقطها cleanup
        # (كان الفشل بلا expires_at = صف + مجلد + status يتيمون للأبد)
        shutil.rmtree(outdir, ignore_errors=True)
        write_status(job_id, "failed", message=str(e)[:300])
        with SessionLocal() as db:
            j = db.get(Job, job_id)
            if j:
                j.status = 'failed'
                j.error = str(e)[:500]
                j.expires_at = utcnow() + timedelta(hours=1)
                db.commit()
    finally:
        ex.cleanup()

def _recover_stale_jobs(r):
    """استرجاع عند الإقلاع: وظائف عالقة في downloading/preparing (>10 دقائق)
    — مات العامل في منتصفها وخرجت من طابور Redis للأبد (blpop لا يعيد التسليم)."""
    try:
        with SessionLocal() as db:
            stale = db.query(Job).filter(
                Job.status.in_(['downloading', 'preparing']),
                Job.created_at < utcnow() - timedelta(minutes=10),
            ).all()
            for j in stale:
                j.status = 'waiting'
                db.commit()
                r.rpush('yjdownload:jobs', j.id)
                print(f"recovered stale job {j.id}")
    except Exception:  # noqa: BLE001
        pass

def _safe_process(jid):
    try:
        process_job(jid)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("worker").exception("job %s crashed", jid)


def main():
    import redis
    r = redis.Redis.from_url(settings.redis_url)
    _recover_stale_jobs(r)
    # ⚡ معالجة متوازية: حتى 20 مهمة بنفس الوقت — بلا طابور، الكل يتحمل دفعة واحدة
    executor = ThreadPoolExecutor(max_workers=20)
    while True:
        try:
            item = r.blpop('yjdownload:jobs', timeout=5)
            if item:
                jid = item[1].decode()
                executor.submit(_safe_process, jid)
        except Exception:  # noqa: BLE001
            time.sleep(5)

if __name__ == '__main__':
    main()
