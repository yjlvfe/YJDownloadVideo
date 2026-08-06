"""اختبارات نظام اختيار التحميل: الجودات الديناميكية + تنظيف الحالات المعلقة + الحذف الفوري."""
import time
from pathlib import Path

from app import bot


def test_quality_label_dynamic_digits():
    """الدقة الرقمية من الفحص الفعلي تُعرض كـ 2160p — لا أسماء ثابتة فقط."""
    assert bot._quality_label("best") == "⭐ الأفضل"
    assert bot._quality_label("audio") == "🎵 صوت"
    assert bot._quality_label("2160") == "2160p"
    assert bot._quality_label("360") == "360p"


def test_offered_heights_from_real_formats():
    """أزرار الجودات تُبنى من جودات المقطع الحقيقية فقط — تنازليًا وبلا تكرار."""
    formats = [
        {"id": "137", "height": 1080, "label": "1080p / mp4", "size": 100},
        {"id": "136", "height": 720, "label": "720p / mp4", "size": 50},
        {"id": "18", "height": 360, "label": "360p / mp4", "size": 10},
        {"id": "sb1", "height": None, "label": "storyboard", "size": None},
        {"id": "140", "height": None, "label": "audio", "size": 5},
    ]
    assert bot._offered_heights(formats) == [1080, 720, 360]
    # بلا جودات حقيقية → لا أزرار دقات
    assert bot._offered_heights([]) == []
    assert bot._offered_heights([{"id": "x", "label": "audio"}]) == []


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
