import time, os, shutil
from .config import settings
from .db import SessionLocal, Job, utcnow

def cleanup_once():
    with SessionLocal() as db:
        rows=db.query(Job).filter(Job.expires_at!=None, Job.expires_at<utcnow()).all()
        for j in rows:
            if j.file_path:
                # حذف الملف + أي ملفات مصاحبة (مثل mp3 المحوّل) + المجلد كاملًا
                folder=os.path.dirname(j.file_path)
                if folder and os.path.isdir(folder):
                    shutil.rmtree(folder, ignore_errors=True)
                else:
                    try: os.remove(j.file_path)
                    except FileNotFoundError: pass
            try: os.remove(os.path.join(settings.status_dir, f"{j.id}.json"))
            except (FileNotFoundError, OSError): pass
            db.delete(j)
        db.commit()

def main():
    while True:
        cleanup_once(); time.sleep(settings.cleanup_interval_seconds)
if __name__=='__main__': main()
