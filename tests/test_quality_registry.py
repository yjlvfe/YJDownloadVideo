import pytest

from app.media_choice import (
    quality_key_for_selector,
    quality_options,
    selector_for_quality,
)
from app.telegram_flow import parse_callback


def test_quality_registry_has_the_complete_public_contract() -> None:
    options = quality_options(include_audio=True)
    assert [item.key for item in options] == ["best", "1080", "720", "480", "audio"]
    assert [item.label for item in options] == [
        "⭐ الأفضل",
        "1080p",
        "720p",
        "480p",
        "🎧 صوت MP3",
    ]


def test_every_video_quality_has_a_combined_media_fallback() -> None:
    for key in ("best", "1080", "720", "480"):
        selector = selector_for_quality(key)
        assert "/best" in selector
        assert quality_key_for_selector(selector) == key


def test_audio_quality_is_explicit_and_round_trips() -> None:
    selector = selector_for_quality("audio")
    assert selector == "bestaudio/best"
    assert quality_key_for_selector(selector) == "audio"


def test_unknown_quality_is_rejected() -> None:
    with pytest.raises(ValueError, match="quality"):
        selector_for_quality("2160")


def test_best_quality_callback_is_accepted() -> None:
    assert parse_callback("pick:q:abcdefgh:best") == ("quality", "abcdefgh", "best")


def test_arbitrary_quality_callback_is_rejected() -> None:
    assert parse_callback("pick:q:abcdefgh:../../secret") is None
    assert parse_callback("pick:q:abcdefgh:2160") is None
