import time, os, shutil
from datetime import timedelta
from pathlib import Path
from sqlalchemy import or_
from .config import settings
from .db import SessionLocal, Job, utcnow

def cleanup_once():
    now = utcnow()
    with SessionLocal() as db:
        # 🧹 القاعدة الجديدة: المنتهية + الفاشلة (أقدم من ساعة) + المنتظرة العالقة (24h)
        # + العالقة في تحميل/تجهيز (6h — عامل مات في منتصفها)
        rows = db.query(Job).filter(or_(
            (Job.expires_at != None) & (Job.expires_at < now),          # noqa: E711
            (Job.status == 'failed') & (Job.created_at < now - timedelta(hours=1)),
            (Job.status == 'waiting') & (Job.created_at < now - timedelta(hours=24)),
            (Job.status.in_(['downloading', 'preparing']))
            & (Job.created_at < now - timedelta(hours=6)),
        )).all()
        live_ids = {r.id for r in db.query(Job.id).all()}
        for j in rows:
            # حذف مجلد المهمة كاملًا (الملف + البقايا الجزئية + المصغرات)
            folder = Path(settings.storage_dir) / j.id
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
            elif j.file_path:
                try:
                    os.remove(j.file_path)
                except FileNotFoundError:
                    pass
            try:
                os.remove(Path(settings.status_path) / f"{j.id}.json")
            except (FileNotFoundError, OSError):
                pass
            db.delete(j)
        db.commit()
    # 🧹 أيتام بلا صفوف: ملفات حالة/مجلدات لمهام حُذفت صفوفها (مثل موت العامل
    # في منتصف المعالجة بعد حذف الصف) — لا تتراكم أبدًا
    # (أقدم من 10 دقائق فقط — حماية من سباق مهمة جديدة تكتب ملفها للتو)
    try:
        cutoff = time.time() - 600
        for f in Path(settings.status_path).glob("*.json"):
            if f.stem not in live_ids and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
        for d in Path(settings.storage_dir).iterdir():
            if d.is_dir() and d.name not in live_ids and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except OSError:
        pass

def main():
    while True:
        cleanup_once()
        time.sleep(settings.cleanup_interval_seconds)

if __name__ == '__main__':
    main()
