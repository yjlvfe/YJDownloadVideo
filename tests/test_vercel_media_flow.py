from pathlib import Path

import pytest

from app.vercel_media_flow import (
    CALLBACK_AUDIO,
    CALLBACK_VIDEO,
    callback_context,
    cleanup_vercel_temp,
    extract_first_url,
    progress_bar,
    quality_buttons,
    safe_media_filename,
    selected_format,
    type_buttons,
)


def test_extract_first_url_accepts_a_general_http_media_page():
    assert (
        extract_first_url("شوف هذا https://example.com/watch?id=7 ثم خبرني")
        == "https://example.com/watch?id=7"
    )


def test_type_buttons_have_only_video_and_audio():
    assert type_buttons() == {
        "inline_keyboard": [[
            {"text": "🎬 فيديو", "callback_data": CALLBACK_VIDEO},
            {"text": "🎧 صوت MP3", "callback_data": CALLBACK_AUDIO},
        ]]
    }


def test_quality_buttons_show_only_real_deliverable_heights():
    formats = [
        {"height": 360, "size": 8_000_000},
        {"height": 360, "size": 10_000_000},
        {"height": 720, "size": 42_000_000},
        {"height": 1080, "size": 90_000_000},
        {"height": None, "size": 1_000_000},
    ]
    keyboard = quality_buttons(formats, max_bytes=49_000_000)
    flat = [button for row in keyboard["inline_keyboard"] for button in row]
    assert [button["callback_data"] for button in flat] == [
        "media:q:360",
        "media:q:720",
    ]
    assert "1080" not in str(keyboard)


def test_callback_context_reads_url_and_owner_from_replied_message():
    callback = {
        "from": {"id": 44},
        "message": {
            "chat": {"id": 55},
            "message_id": 99,
            "reply_to_message": {
                "from": {"id": 44},
                "text": "https://cdn.example/video.mp4",
            },
        },
    }
    context = callback_context(callback)
    assert context.chat_id == 55
    assert context.preview_message_id == 99
    assert context.owner_id == 44
    assert context.url == "https://cdn.example/video.mp4"


def test_callback_context_rejects_a_different_user():
    callback = {
        "from": {"id": 45},
        "message": {
            "chat": {"id": 55},
            "message_id": 99,
            "reply_to_message": {
                "from": {"id": 44},
                "text": "https://cdn.example/video.mp4",
            },
        },
    }
    with pytest.raises(PermissionError):
        callback_context(callback)


def test_progress_bar_uses_twelve_cells():
    assert progress_bar(0) == "░" * 12
    assert progress_bar(50) == "█" * 6 + "░" * 6
    assert progress_bar(100) == "█" * 12


def test_safe_audio_name_uses_source_title():
    assert safe_media_filename(' عنوان/فيديو: "تجربة" ', ".mp3") == "عنوان فيديو تجربة.mp3"


def test_selected_video_quality_builds_an_exact_height_cap():
    assert selected_format("media:q:720") == (
        "bestvideo[height<=720]+bestaudio/best[height<=720]"
    )
    assert selected_format("media:q:original") == "best"


@pytest.mark.parametrize("value", ["media:q:nope", "media:q:99", "other:720"])
def test_selected_video_quality_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        selected_format(value)


def test_cleanup_removes_only_application_temp_directories(tmp_path, monkeypatch):
    import app.vercel_media_flow as flow

    app_dir = tmp_path / "yjdownload-abc"
    app_dir.mkdir()
    (app_dir / "video.mp4").write_bytes(b"x" * 8)
    other = tmp_path / "unrelated"
    other.mkdir()
    (other / "keep.txt").write_text("keep")

    real_path = flow.Path

    class FakePath:
        def __new__(cls, value):
            if value == "/tmp":
                return real_path(tmp_path)
            return real_path(value)

    monkeypatch.setattr(flow, "Path", FakePath)
    cleanup_vercel_temp()

    assert not app_dir.exists()
    assert other.exists()
