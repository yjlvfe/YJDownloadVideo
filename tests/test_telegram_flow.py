from app.media_choice import QualityOption
from app.telegram_flow import (
    kind_keyboard,
    parse_callback,
    progress_text,
    quality_keyboard,
)


def test_kind_keyboard_places_video_and_audio_under_preview():
    markup = kind_keyboard("abc123")

    assert markup == {
        "inline_keyboard": [[
            {"text": "🎬 فيديو", "callback_data": "pick:v:abc123"},
            {"text": "🎧 صوت MP3", "callback_data": "pick:a:abc123"},
        ]]
    }


def test_quality_keyboard_uses_only_inspected_qualities():
    qualities = [
        QualityOption("360", "360p", "selector-360", 10),
        QualityOption("720", "720p", "selector-720", 20),
    ]

    markup = quality_keyboard("abc123", qualities)

    values = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    assert values == ["pick:q:abc123:360", "pick:q:abc123:720"]


def test_callback_parser_accepts_only_supported_shapes():
    assert parse_callback("pick:v:abc123") == ("video", "abc123", None)
    assert parse_callback("pick:a:abc123") == ("audio", "abc123", None)
    assert parse_callback("pick:q:abc123:720") == ("quality", "abc123", "720")
    assert parse_callback("mp3:old") is None
    assert parse_callback("pick:q:abc123:720:extra") is None


def test_progress_text_is_consistent_for_every_stage():
    assert progress_text("downloading", 40) == "⬇️ جارٍ التحميل [█████░░░░░░░] 40%"
    assert progress_text("preparing", 20) == "🔧 جارٍ تجهيز الفيديو [██░░░░░░░░░░] 20%"
    assert progress_text("converting", 70) == "🎧 جارٍ التحويل إلى MP3 [████████░░░░] 70%"
    assert progress_text("uploading", 100) == "⬆️ جارٍ الرفع [████████████] 100%"
