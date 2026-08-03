from io import BytesIO

import pytest

from app.telegram_api import ProgressFile, TelegramAPI, media_payload


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


def test_video_payload_is_clean_without_caption_or_buttons():
    payload = media_payload("video", chat_id=7, title="ignored")

    assert payload == {"chat_id": 7, "supports_streaming": "true"}
    assert "caption" not in payload
    assert "reply_markup" not in payload


def test_audio_payload_uses_title_without_caption_or_buttons():
    payload = media_payload("audio", chat_id=7, title="اسم المقطع")

    assert payload == {"chat_id": 7, "title": "اسم المقطع"}
    assert "caption" not in payload
    assert "reply_markup" not in payload


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
