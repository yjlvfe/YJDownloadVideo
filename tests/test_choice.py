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


def test_offered_qualities_from_real_formats():
    """أزرار الجودات تُبنى من جودات المقطع الحقيقية فقط — تنازليًا وبلا تكرار، مع الأحجام."""
    formats = [
        {"id": "137", "height": 1080, "label": "1080p / mp4", "size": 100},
        {"id": "136", "height": 720, "label": "720p / mp4", "size": 50},
        {"id": "18", "height": 360, "label": "360p / mp4", "size": 10},
        {"id": "sb1", "height": None, "label": "storyboard", "size": None},
        {"id": "140", "height": None, "label": "audio", "size": 5},
    ]
    out = bot._offered_qualities(formats)
    assert [q["height"] for q in out] == [1080, 720, 360]
    assert out[0]["size"] == 100
    assert out[1]["size"] == 50
    # بلا جودات حقيقية → لا أزرار دقات
    assert bot._offered_qualities([]) == []
    assert bot._offered_qualities([{"id": "x", "label": "audio"}]) == []


def test_fmt_mb_size_in_button():
    """حجم كل جودة يظهر داخل الزر بصيغة مقروءة."""
    assert bot._fmt_mb(229 * 1048576) == "229MB"
    assert bot._fmt_mb(int(11.3 * 1048576)) == "11.3MB"
    assert bot._fmt_mb(int(0.5 * 1048576)) == "0.5MB"
    assert bot._fmt_mb(None) == ""
    assert bot._fmt_mb(0) == ""


def test_video_meta_extracts_real_metadata(tmp_path):
    """المعاينة + المدة/الأبعاد تُستخرج من الملف نفسه — أساس عرض الفيديو كفيديو لا كملف."""
    import subprocess
    src = tmp_path / "clip.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(src)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    meta = bot._video_meta(src, tmp_path)
    assert meta.get("width") == 640
    assert meta.get("height") == 360
    assert meta.get("duration") == 3
    assert meta.get("thumbnail") and Path(meta["thumbnail"]).is_file()
    assert Path(meta["thumbnail"]).stat().st_size > 0


def test_video_meta_tolerates_missing_file(tmp_path):
    """لا يرمي استثناء على ملف غير موجود — الإرسال يبقى يعمل بدونه."""
    meta = bot._video_meta(tmp_path / "missing.mp4", tmp_path)
    assert isinstance(meta, dict)


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
