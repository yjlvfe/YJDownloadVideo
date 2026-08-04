from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.youtube_innertube as module
from app.youtube_innertube import (
    YouTubeInnertubeError,
    extract_youtube_video_id,
    run_youtube_innertube,
)


def test_extract_youtube_video_id_supports_common_urls():
    assert extract_youtube_video_id("https://youtu.be/kTRs4ycFp_o?x=1") == "kTRs4ycFp_o"
    assert extract_youtube_video_id("https://youtube.com/shorts/T7DA7VTZ_Zs") == "T7DA7VTZ_Zs"
    assert extract_youtube_video_id("https://youtube.com/watch?v=kTRs4ycFp_o") == "kTRs4ycFp_o"


def test_innertube_inspect_parses_last_json_line(monkeypatch, tmp_path):
    payload = {"source": "youtubei-js", "formats": [{"id": "720"}]}
    completed = SimpleNamespace(
        returncode=0,
        stdout="diagnostic\n" + json.dumps(payload) + "\n",
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    result = run_youtube_innertube(
        action="inspect",
        url="https://youtu.be/kTRs4ycFp_o",
        output_dir=tmp_path,
    )
    assert result == payload


def test_innertube_download_rejects_path_outside_job_directory(monkeypatch, tmp_path):
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"media")
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"path": str(outside)}) + "\n",
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError, match="غير آمن"):
        run_youtube_innertube(
            action="download",
            url="https://youtu.be/kTRs4ycFp_o",
            output_dir=tmp_path,
        )


def test_innertube_download_enforces_final_size(monkeypatch, tmp_path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"x" * 20)
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps({"path": str(output), "ext": "mp4"}) + "\n",
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError, match="حجم"):
        run_youtube_innertube(
            action="download",
            url="https://youtu.be/kTRs4ycFp_o",
            output_dir=tmp_path,
            max_bytes=10,
        )
    assert not output.exists()


def test_innertube_failure_redacts_secret_query(monkeypatch, tmp_path):
    completed = SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="failed at https://secret.example/path?token=value",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(YouTubeInnertubeError) as caught:
        run_youtube_innertube(
            action="inspect",
            url="https://youtu.be/kTRs4ycFp_o",
            output_dir=tmp_path,
        )
    message = str(caught.value)
    assert "token=value" not in message
    assert "[REDACTED_QUERY]" in message
