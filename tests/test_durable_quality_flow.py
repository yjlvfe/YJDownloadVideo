from app.durable_runtime import (
    actual_video_qualities,
    quality_payloads,
    qualities_from_payloads,
)


def test_actual_video_qualities_include_best_then_only_available_heights() -> None:
    raw = [
        {"height": 360, "vcodec": "h264", "filesize": 1_000_000},
        {"height": 720, "vcodec": "h264", "filesize": 3_000_000},
        {"height": 720, "vcodec": "vp9", "filesize": 4_000_000},
        {"height": 1080, "vcodec": "none", "filesize": 500_000},
        {"height": 2160, "vcodec": "h264", "filesize": 10_000_000},
    ]

    qualities = actual_video_qualities(raw)

    assert [item.key for item in qualities] == ["best", "720", "360"]
    assert qualities[1].size == 4_000_000
    assert "1080" not in {item.key for item in qualities}
    assert "2160" not in {item.key for item in qualities}


def test_quality_payload_round_trip_is_json_safe() -> None:
    qualities = actual_video_qualities([
        {"height": 480, "vcodec": "h264", "filesize": 2_000_000},
    ])
    payloads = quality_payloads(qualities)

    assert payloads == [
        {
            "key": "best",
            "label": "⭐ الأفضل",
            "selector": "bestvideo*+bestaudio/best",
            "size": None,
        },
        {
            "key": "480",
            "label": "480p",
            "selector": "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "size": 2_000_000,
        },
    ]
    assert qualities_from_payloads(payloads) == qualities


def test_no_format_metadata_still_has_a_truthful_best_button() -> None:
    qualities = actual_video_qualities([])
    assert [item.key for item in qualities] == ["best"]
