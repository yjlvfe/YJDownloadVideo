import pytest

from app.media_choice import (
    quality_key_for_selector,
    quality_options,
    selector_for_quality,
)
from app.telegram_flow import parse_callback


def test_quality_registry_contains_only_universal_static_choices() -> None:
    options = quality_options(include_audio=True)
    assert [item.key for item in options] == ["best", "audio"]
    assert [item.label for item in options] == ["⭐ الأفضل", "🎧 صوت MP3"]


def test_every_dynamic_video_quality_has_a_combined_media_fallback() -> None:
    for key in ("best", "480", "720", "1080", "2160", "4320"):
        selector = selector_for_quality(key)
        assert "/best" in selector
        assert quality_key_for_selector(selector) == key


def test_audio_quality_is_explicit_and_round_trips() -> None:
    selector = selector_for_quality("audio")
    assert selector == "bestaudio/best"
    assert quality_key_for_selector(selector) == "audio"


def test_out_of_range_quality_is_rejected() -> None:
    with pytest.raises(ValueError, match="quality"):
        selector_for_quality("9999")


def test_best_and_real_quality_callbacks_are_accepted() -> None:
    assert parse_callback("pick:q:abcdefgh:best") == ("quality", "abcdefgh", "best")
    assert parse_callback("pick:q:abcdefgh:2160") == ("quality", "abcdefgh", "2160")


def test_unsafe_or_out_of_range_quality_callback_is_rejected() -> None:
    assert parse_callback("pick:q:abcdefgh:../../secret") is None
    assert parse_callback("pick:q:abcdefgh:9999") is None