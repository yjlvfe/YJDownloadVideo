from pathlib import Path

from app.asgi import media_response_metadata


def test_audio_download_keeps_audio_extension_and_mime_type(tmp_path: Path) -> None:
    path = tmp_path / "source.m4a"
    path.write_bytes(b"audio")

    filename, media_type = media_response_metadata(path, "مقطع صوتي")

    assert filename == "مقطع صوتي.m4a"
    assert media_type in {"audio/mp4", "audio/x-m4a"}


def test_video_download_keeps_video_extension_and_mime_type(tmp_path: Path) -> None:
    path = tmp_path / "source.webm"
    path.write_bytes(b"video")

    filename, media_type = media_response_metadata(path, "فيديو")

    assert filename == "فيديو.webm"
    assert media_type == "video/webm"
