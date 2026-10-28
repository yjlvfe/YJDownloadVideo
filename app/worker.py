import time, shutil
import logging

log = logging.getLogger("worker")
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
    write_status(job_id, "connecting", 0, message="جارٍ الاتصال بالسيرفر وجلب المقطع...")

    # التقدم يُكتب في status.json فقط — لا مساس بـ SQLite عند كل نبضة
    # (كان commit لكل حدث تقدم ~1/ثانية = تنافس قفل بلا أي فائدة)
    def hook(d):
        if d['status'] == 'downloading':
            # الحجم الكلي المؤكد فقط من Content-Length الحقيقي — لا نستخدم التقدير المتذبذب للبثوث
            total = d.get('total_bytes') or 0
            done = d.get('downloaded_bytes') or 0
            speed = d.get('speed') or 0
            eta = d.get('eta') or 0
            frag_index = d.get('fragment_index')
            frag_count = d.get('fragment_count')

            # 🎯 نسبة تقدّم ذكية وسلسة:
            # 1. إذا كان تحميل أجزاء (HLS / DASH) نعتمد على نسبة الأجزاء المكتملة
            # 2. إذا كان الحجم الكلي معلوماً، نعتمد على done / total
            # 3. نضمن ألا يتجمد عند 0% لحظة وصول أول بايت (max 1%)
            pct = 0
            if frag_count and frag_index is not None and frag_count > 0:
                pct = max(1, min(99, int((frag_index + 1) / frag_count * 100)))
            elif total > 0 and done > 0:
                pct = max(1, min(99, int(done / total * 100)))
            else:
                pct_str = (d.get('_percent_str') or '').strip().rstrip('%')
                try:
                    pct = max(1 if done > 0 else 0, min(99, int(float(pct_str))))
                except (ValueError, TypeError):
                    pct = 1 if done > 0 else 0

            write_status(job_id, "downloading", pct, size=done, total=total, speed=int(speed), eta=int(eta))
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
        log.exception("job %s crashed", jid)


def main():
    import redis
    r = redis.Redis.from_url(settings.redis_url)
    _recover_stale_jobs(r)
    # حدّ التوازي على مستوى المهام حتى لا تتنافس 10 مهام × 16 أجزاء HLS
    # على نفس الـ VPN/CDN؛ هذا كان سبب بطء بدء التنزيل وتذبذب السرعة.
    executor = ThreadPoolExecutor(max_workers=4)
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
