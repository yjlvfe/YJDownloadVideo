"""اختبارات مسار الفشل في العامل: لا بقايا على القرص + انتهاء صلاحية ليلتقطها cleanup."""
from pathlib import Path

from app.config import settings
from app.db import init_db, SessionLocal, Job
from app import worker


def _fail_download(*args, **kwargs):
    raise RuntimeError("فشل تحميل متعمد للاختبار")


def test_failure_sets_expiry_and_deletes_folder(monkeypatch):
    """فشل التحميل: مجلد المهمة يُحذف فورًا + expires_at يُضبط (كان يبقى 1GB يتيم للأبد)."""
    init_db()
    jid = "fail-1"
    with SessionLocal() as db:
        db.add(Job(id=jid, url="https://example.com/v", status="waiting", owner_key="t"))
        db.commit()
    folder = Path(settings.storage_path) / jid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "partial.mp4").write_bytes(b"partial-data")

    monkeypatch.setattr(worker.UniversalExtractor, "download", _fail_download)
    worker.process_job(jid)

    with SessionLocal() as db:
        j = db.get(Job, jid)
        assert j is not None
        assert j.status == "failed"
        assert j.expires_at is not None  # ← cleanup سيلتقطه بعد ساعة
        assert "فشل تحميل متعمد" in (j.error or "")
    assert not folder.exists()  # ← لا بقايا جزئية على القرص


def test_success_path_records_file(monkeypatch):
    """نجاح التحميل: status=ready + expires_at + توكن تحميل."""
    init_db()
    jid = "ok-1"
    with SessionLocal() as db:
        db.add(Job(id=jid, url="https://example.com/v", status="waiting", owner_key="t"))
        db.commit()
    folder = Path(settings.storage_path) / jid
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "clip.mp4"
    video.write_bytes(b"fake-mp4")

    def _ok_download(self, url, outdir, preferred="best", **kw):
        return {"path": str(video), "title": "clip", "duration": 10, "thumbnail": ""}

    monkeypatch.setattr(worker.UniversalExtractor, "download", _ok_download)
    worker.process_job(jid)

    with SessionLocal() as db:
        j = db.get(Job, jid)
        assert j.status == "ready"
        assert j.file_path == str(video)
        assert j.download_token  # توكن صالح
        assert j.expires_at is not None
