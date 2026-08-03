"""اختبارات نظام اختيار التحميل: الجودات الصالحة + تنظيف الحالات المعلقة + الحذف الفوري."""
import time
from pathlib import Path

from app import bot


def test_valid_qualities_whitelist():
    assert bot._VALID_QUALITIES == {"best", "1080", "720", "480", "audio"}


def test_pending_cleanup_removes_expired():
    bot.PENDING.clear()
    bot.PENDING["old"] = {"user_id": 1, "chat_id": 1, "url": "u",
                          "created": time.time() - bot._PICK_TTL - 10}
    bot.PENDING["fresh"] = {"user_id": 1, "chat_id": 1, "url": "u",
                            "created": time.time()}
    bot._cleanup_pending()
    assert "old" not in bot.PENDING
    assert "fresh" in bot.PENDING
    bot.PENDING.clear()


def test_delete_job_files_removes_file_and_dir(tmp_path):
    job_dir = tmp_path / "job1"
    job_dir.mkdir()
    video = job_dir / "video.mp4"
    video.write_bytes(b"fake-video")

    class FakeJob:
        id = "job1"
        file_path = str(video)

    bot.delete_job_files(FakeJob())
    assert not video.exists()
    assert not job_dir.exists()


def test_delete_job_files_tolerates_missing_file(tmp_path):
    class FakeJob:
        id = "missing"
        file_path = str(tmp_path / "never-existed.mp4")

    bot.delete_job_files(FakeJob())  # يجب ألا يرمي استثناء
