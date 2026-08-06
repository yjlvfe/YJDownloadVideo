from io import BytesIO
from pathlib import Path

import pytest

from app.telegram_api import ProgressFile, TelegramAPI, media_payload
from app.video_delivery import VideoUploadMetadata


def test_progress_file_streams_and_reports_bytes():
    seen = []
    wrapped = ProgressFile(
        BytesIO(b"abcdefghij"),
        total=10,
        callback=lambda done, total: seen.append((done, total)),
    )

    assert wrapped.read(4) == b"abcd"
    assert wrapped.read(3) == b"efg"
    assert wrapped.read() == b"hij"
    assert seen[-1] == (10, 10)


def test_video_payload_includes_real_identity_without_caption_or_buttons(tmp_path: Path):
    thumbnail = tmp_path / "thumb.jpg"
    thumbnail.write_bytes(b"jpeg")
    metadata = VideoUploadMetadata(
        duration=840,
        width=1280,
        height=720,
        thumbnail=thumbnail,
    )

    payload = media_payload(
        "video",
        chat_id=7,
        title="ignored",
        video_metadata=metadata,
    )

    assert payload == {
        "chat_id": 7,
        "supports_streaming": "true",
        "duration": 840,
        "width": 1280,
        "height": 720,
    }
    assert "caption" not in payload
    assert "reply_markup" not in payload


def test_audio_payload_uses_title_without_caption_or_buttons():
    payload = media_payload("audio", chat_id=7, title="اسم المقطع")

    assert payload == {"chat_id": 7, "title": "اسم المقطع"}
    assert "caption" not in payload
    assert "reply_markup" not in payload


@pytest.mark.asyncio
async def test_send_video_file_forwards_thumbnail_and_metadata(monkeypatch, tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"media")
    thumbnail = tmp_path / "thumb.jpg"
    thumbnail.write_bytes(b"jpeg")
    metadata = VideoUploadMetadata(840, 1280, 720, thumbnail)
    captured = {}

    monkeypatch.setattr("app.telegram_api.prepare_video_upload", lambda path: metadata)

    api = TelegramAPI(token="secret", base_url="http://127.0.0.1:8081")

    async def fake_upload(method, field, path, payload, *, progress=None, extra_files=None):
        captured.update({
            "method": method,
            "field": field,
            "path": path,
            "payload": payload,
            "extra_files": extra_files,
        })
        return {"video": {"duration": 840, "width": 1280, "height": 720}}

    monkeypatch.setattr(api, "_upload", fake_upload)

    await api.send_video_file(7, video)

    assert captured["method"] == "sendVideo"
    assert captured["field"] == "video"
    assert captured["payload"]["duration"] == 840
    assert captured["payload"]["width"] == 1280
    assert captured["payload"]["height"] == 720
    assert captured["payload"]["supports_streaming"] == "true"
    assert captured["extra_files"] == {"thumbnail": thumbnail}


@pytest.mark.asyncio
async def test_ready_probes_listener_root_without_bot_token(monkeypatch):
    requested_urls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, url):
            requested_urls.append(url)
            return object()

    monkeypatch.setattr(
        "app.telegram_api.httpx.AsyncClient",
        lambda **kwargs: FakeClient(),
    )

    api = TelegramAPI(token="secret", base_url="http://127.0.0.1:8081")

    assert await api.ready() is True
    assert requested_urls == ["http://127.0.0.1:8081/"]
    assert "secret" not in requested_urls[0]
