from scripts.acceptance_media import (
    ACCEPTANCE_URLS,
    classify_api_failure,
    planned_quality_keys,
)


def test_manifest_contains_all_supplied_urls() -> None:
    assert ACCEPTANCE_URLS == [
        "https://vm.tiktok.com/ZGdxjnte8/",
        "https://www.instagram.com/reel/DbkuNu8oMA5/?igsh=MWVuMjJlOWszdHQzMw==",
        "https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce",
        "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu",
    ]


def test_every_advertised_button_and_audio_is_planned_once() -> None:
    formats = [
        {"id": "best", "label": "best"},
        {"id": "720", "label": "720p"},
        {"id": "480", "label": "480p"},
        {"id": "720", "label": "duplicate"},
    ]
    assert planned_quality_keys(formats, include_audio=True) == [
        "best",
        "720",
        "480",
        "audio",
    ]


def test_youtube_circuit_and_bot_blocks_are_failures_not_success() -> None:
    assert classify_api_failure("youtube_circuit_open") == "youtube_circuit_open"
    assert classify_api_failure("youtube_bot_check") == "youtube_bot_check"
    assert classify_api_failure("HTTP Error 429") == "youtube_rate_limited"
    assert classify_api_failure("anything else") == "unclassified"
