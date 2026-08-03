from pathlib import Path

from app.vercel_runtime import (
    cleanup_application_storage,
    media_duration,
    retained_application_media_stats,
)


def test_cleanup_removes_all_application_storage_but_not_unrelated_tmp(tmp_path):
    tmp_root = tmp_path / "tmp"
    storage = tmp_path / "storage"
    tmp_root.mkdir()
    storage.mkdir()

    interrupted = tmp_root / "yjdownload-old"
    interrupted.mkdir()
    (interrupted / "video.mp4").write_bytes(b"video")
    preview = tmp_root / "yjpreview-old"
    preview.mkdir()
    (preview / "preview.jpg").write_bytes(b"image")
    unrelated = tmp_root / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep")
    old_job = storage / "old-job-id"
    old_job.mkdir()
    (old_job / "audio.mp3").write_bytes(b"audio")

    cleanup_application_storage(storage, tmp_root=tmp_root)

    assert not interrupted.exists()
    assert not preview.exists()
    assert not old_job.exists()
    assert unrelated.exists()


def test_retained_stats_include_temp_and_storage_files(tmp_path):
    tmp_root = tmp_path / "tmp"
    storage = tmp_path / "storage"
    tmp_root.mkdir()
    storage.mkdir()
    app_temp = tmp_root / "yjdownload-one"
    app_temp.mkdir()
    (app_temp / "one.mp4").write_bytes(b"123")
    old_job = storage / "job"
    old_job.mkdir()
    (old_job / "two.mp3").write_bytes(b"4567")

    assert retained_application_media_stats(storage, tmp_root=tmp_root) == (2, 7)


def test_media_duration_uses_ffmpeg_header_without_ffprobe(tmp_path):
    calls = []

    class Result:
        stderr = "Duration: 00:01:23.50, start: 0.000000, bitrate: 1200 kb/s"

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    duration = media_duration(tmp_path / "video.mp4", ffmpeg_bin="ffmpeg", runner=fake_run)

    assert duration == 83.5
    assert calls == [["ffmpeg", "-hide_banner", "-i", str(tmp_path / "video.mp4")]]
