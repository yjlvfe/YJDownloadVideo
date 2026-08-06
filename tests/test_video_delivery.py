from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.config import ffmpeg_location
from app.video_delivery import prepare_video_upload


def test_prepare_video_upload_extracts_real_metadata_and_thumbnail(monkeypatch, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"media")

    def fake_run(command, **kwargs):
        if "ffprobe" in Path(command[0]).name:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "streams": [{"width": 1280, "height": 720, "duration": "840.4"}],
                    "format": {"duration": "840.4"},
                }),
                stderr="",
            )
        Path(command[-1]).write_bytes(b"jpeg")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.video_delivery.subprocess.run", fake_run)
    monkeypatch.setattr("app.video_delivery.ffprobe_location", lambda: "ffprobe")
    monkeypatch.setattr("app.video_delivery.ffmpeg_location", lambda: "ffmpeg")

    metadata = prepare_video_upload(video, tmp_path)

    assert metadata.duration == 840
    assert metadata.width == 1280
    assert metadata.height == 720
    assert metadata.thumbnail is not None
    assert metadata.thumbnail.is_file()


def test_prepare_video_upload_works_with_real_ffmpeg(tmp_path: Path):
    video = tmp_path / "real.mp4"
    subprocess.run(
        [
            ffmpeg_location(),
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=25:d=2",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    metadata = prepare_video_upload(video, tmp_path)

    assert metadata.duration is not None and metadata.duration >= 1
    assert metadata.width == 160
    assert metadata.height == 90
    assert metadata.thumbnail is not None
    assert metadata.thumbnail.is_file()
    assert 0 < metadata.thumbnail.stat().st_size <= 200_000


def test_prepare_video_upload_degrades_without_failing(monkeypatch, tmp_path: Path):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"media")

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="not-json", stderr="failed")

    monkeypatch.setattr("app.video_delivery.subprocess.run", fake_run)
    monkeypatch.setattr("app.video_delivery.ffprobe_location", lambda: "ffprobe")
    monkeypatch.setattr("app.video_delivery.ffmpeg_location", lambda: "ffmpeg")

    metadata = prepare_video_upload(video, tmp_path)

    assert metadata.duration is None
    assert metadata.width is None
    assert metadata.height is None
    assert metadata.thumbnail is None