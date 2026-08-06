"""اختبارات غلاف YouTube.js (innertube): استخراج ID + تحليل JSON + أمان المسار + الحد الأقصى."""
import json
from types import SimpleNamespace

import pytest

import app.youtube_innertube as module
from app.youtube_innertube import (
    YouTubeInnertubeError,
    extract_youtube_video_id,
    innertube_available,
    run_youtube_innertube,
)


def test_extract_youtube_video_id_supports_common_urls():
    assert extract_youtube_video_id("https://youtu.be/kTRs4ycFp_o?x=1") == "kTRs4ycFp_o"
    assert extract_youtube_video_id("https://youtube.com/shorts/T7DA7VTZ_Zs") == "T7DA7VTZ_Zs"
    assert extract_youtube_video_id("https://youtube.com/watch?v=kTRs4ycFp_o") == "kTRs4ycFp_o"
    with pytest.raises(YouTubeInnertubeError):
        extract_youtube_video_id("https://example.com/not-youtube")


def test_innertube_inspect_parses_last_json_line(monkeypatch, tmp_path):
    payload = {"source": "youtubei-js", "formats": [{"id": "720"}]}
    completed = SimpleNamespace(
        returncode=0,
        stdout="diagnostic\n" + json.dumps(payload) + "\n",
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    result = run_youtube_innertube(
        action="inspect", url="https://youtu.be/kTRs4ycFp_o", output_dir=tmp_path)
    assert result == payload


def test_innertube_rejects_nonzero_exit(monkeypatch, tmp_path):
    completed = SimpleNamespace(returncode=1, stdout="", stderr="boom happened https://x.test/y")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError) as exc:
        run_youtube_innertube(action="inspect", url="https://youtu.be/kTRs4ycFp_o",
                              output_dir=tmp_path)
    # الرسالة معقّمة: لا روابط تسرّب
    assert "https://" not in str(exc.value)


def test_innertube_download_rejects_path_outside_job_directory(monkeypatch, tmp_path):
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"media")
    completed = SimpleNamespace(returncode=0, stdout=json.dumps({"path": str(outside)}) + "\n",
                                stderr="")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError, match="غير آمن"):
        run_youtube_innertube(action="download", url="https://youtu.be/kTRs4ycFp_o",
                              output_dir=tmp_path)


def test_innertube_download_enforces_final_size(monkeypatch, tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"x" * 20)
    completed = SimpleNamespace(returncode=0,
                                stdout=json.dumps({"path": str(output), "ext": "mp4"}) + "\n",
                                stderr="")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError, match="الحد المسموح"):
        run_youtube_innertube(action="download", url="https://youtu.be/kTRs4ycFp_o",
                              output_dir=tmp_path, max_bytes=10)


def test_innertube_missing_file_raises(monkeypatch, tmp_path):
    missing = tmp_path / "never.mp4"
    completed = SimpleNamespace(returncode=0,
                                stdout=json.dumps({"path": str(missing)}) + "\n", stderr="")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError, match="لم ينشئ"):
        run_youtube_innertube(action="download", url="https://youtu.be/kTRs4ycFp_o",
                              output_dir=tmp_path)
