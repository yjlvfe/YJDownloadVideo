from io import BytesIO

from app.telegram_api import ProgressFile, media_payload


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
