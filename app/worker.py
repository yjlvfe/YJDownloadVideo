import time, shutil
import logging
import threading
from collections import deque

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from .config import settings
from .db import SessionLocal, Job, utcnow
from .security import make_token
from .extractor import UniversalExtractor
from .status import write_status

log = logging.getLogger("worker")


class _ProgressWriter:
    """Limit frequent progress writes without delaying stage transitions, and calculate true aggregate throughput."""

    def __init__(self, job_id):
        self.job_id = job_id
        self._last_write = None
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._samples = deque()  # (timestamp, bytes)
        self._last_size = 0
        self._last_total = 0

    def __call__(self, stage, pct=0, **fields):
        with self._lock:
            now = time.monotonic()
            if stage == 'downloading':
                done = fields.get('size', 0)
                if done > 0:
                    self._last_size = done
                    tot = fields.get('total', 0)
                    if tot > 0:
                        self._last_total = tot
                    self._samples.append((now, done))
                    # الاحتفاظ بعينات آخر ثانيتين لحساب سرعة تدفق حقيقية وتفادي تذبذب الخيوط المتزامنة
                    while self._samples and (now - self._samples[0][0]) > 2.0:
                        self._samples.popleft()
                    if len(self._samples) >= 2:
                        dt = now - self._samples[0][0]
                        db = done - self._samples[0][1]
                        if dt >= 0.25 and db > 0:
                            agg_speed = int(db / dt)
                            # في تحميل الأجزاء المتزامنة (HLS)، speed من yt-dlp تمثل سرعة خيط واحد فقط
                            # فنعتمد على سرعة التدفق الإجمالية لكافة الوصلات المتزامنة
                            fields['speed'] = max(agg_speed, int(fields.get('speed', 0)))

                # تقدير دقيق ومستقر للوقت المتبقي (ETA) لتفادي تشوه HLS عند تعدد الوصلات
                reported_eta = fields.get('eta', 0)
                elapsed = now - self._start_time
                if pct > 0 and pct < 100 and elapsed > 2.0:
                    calc_eta = int((100 - pct) * elapsed / pct)
                    if not reported_eta or reported_eta > max(30, calc_eta * 2.5):
                        fields['eta'] = calc_eta

                if self._last_write is not None and now - self._last_write < 0.5:
                    return
                self._last_write = now
            elif stage == 'preparing':
                if not fields.get('size') and self._last_size > 0:
                    fields['size'] = self._last_size
                if not fields.get('total') and self._last_total > 0:
                    fields['total'] = self._last_total

            write_status(self.job_id, stage, pct, **fields)

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

    progress = _ProgressWriter(job_id)
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
            # 3. لا نثبت عند 99% أثناء تحميل الأجزاء المتبقية بل نتدرج حتى 98% ثم 99% عند آخر جزء
            pct = 0
            if frag_count and frag_index is not None and frag_count > 0:
                raw_pct = int((frag_index + 1) / frag_count * 100)
                if (frag_index + 1) < frag_count:
                    pct = max(1, min(98, raw_pct))
                else:
                    pct = 99
            elif total > 0 and done > 0:
                raw_pct = int(done / total * 100)
                if done < total:
                    pct = max(1, min(98, raw_pct))
                else:
                    pct = 99
            else:
                pct_str = (d.get('_percent_str') or '').strip().rstrip('%')
                try:
                    raw_pct = int(float(pct_str))
                    pct = max(1 if done > 0 else 0, min(98, raw_pct))
                except (ValueError, TypeError):
                    pct = 1 if done > 0 else 0

            progress("downloading", pct, size=done, total=total, speed=int(speed), eta=int(eta))
        elif d['status'] in ('finished', 'post-processing'):
            done = int(d.get('downloaded_bytes') or d.get('total_bytes') or 0)
            total = int(d.get('total_bytes') or d.get('downloaded_bytes') or 0)
            progress("preparing", 100, size=done, total=total)

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
            ).all()
            for j in stale:
                status_file = settings.status_path / f"{j.id}.json"
                # لا نقتل مهمة طويلة لمجرد أن عمرها تجاوز 10 دقائق؛
                # ملف الحالة يتجدد مع كل تقدم حقيقي. نعيد فقط المهمة بلا heartbeat.
                fresh = status_file.exists() and (time.time() - status_file.stat().st_mtime) < 900
                if fresh:
                    continue
                j.status = 'waiting'
                db.commit()
                r.rpush('yjdownload:jobs', j.id)
                print(f"recovered stale job {j.id}")
            waiting = db.query(Job).filter(Job.status == 'waiting').all()
            queued = {x.decode() if isinstance(x, bytes) else x for x in r.lrange('yjdownload:jobs', 0, -1)}
            for j in waiting:
                if j.id not in queued:
                    r.rpush('yjdownload:jobs', j.id)
    except Exception:  # noqa: BLE001
        pass

def _safe_process(jid):
    try:
        process_job(jid)
    except Exception:  # noqa: BLE001
        log.exception("job %s crashed", jid)


def main():
    import threading
    import redis
    max_workers = max(1, int(getattr(settings, "worker_max_workers", 1)))
    slots = threading.BoundedSemaphore(max_workers)
    r = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=10,
        socket_timeout=10,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    _recover_stale_jobs(r)
    # نسحب من Redis فقط عند توفر مقعد؛ الطلبات الزائدة تبقى waiting في Redis.
    executor = ThreadPoolExecutor(max_workers=max_workers)
    while True:
        try:
            slots.acquire()
            item = r.blpop('yjdownload:jobs', timeout=5)
            if not item:
                slots.release()
                continue
            jid = item[1].decode()
            future = executor.submit(_safe_process, jid)
            future.add_done_callback(lambda _f: slots.release())
        except Exception:  # noqa: BLE001
            slots.release()
            log.exception("queue dispatcher error")
            time.sleep(5)

if __name__ == '__main__':
    main()
