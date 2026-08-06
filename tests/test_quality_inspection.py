from app.extractor import UniversalExtractor
from app.quality_runtime_patch import install

install()


def test_inspect_returns_rich_real_video_formats(monkeypatch):
    extractor = UniversalExtractor(use_browser=False)

    monkeypatch.setattr(
        extractor,
        "_ytdlp_info",
        lambda url: {
            "title": "4K sample",
            "thumbnail": "https://example.com/thumb.jpg",
            "duration": 60,
            "formats": [
                {"format_id": "sb0", "height": 180, "ext": "mhtml", "vcodec": "none", "acodec": "none"},
                {"format_id": "140", "height": None, "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 5},
                {"format_id": "137", "height": 1080, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "filesize": 80},
                {"format_id": "401", "height": 2160, "ext": "mp4", "vcodec": "av01", "acodec": "none", "filesize_approx": 200},
            ],
        },
    )

    result = extractor.inspect("https://youtu.be/kTRs4ycFp_o")

    assert result["source"] == "ytdlp"
    assert result["title"] == "4K sample"
    assert [item["height"] for item in result["formats"]] == [1080, 2160]
    assert result["formats"][0] == {
        "id": "137",
        "height": 1080,
        "ext": "mp4",
        "vcodec": "avc1",
        "acodec": "none",
        "filesize": 80,
        "filesize_approx": None,
    }


def test_inspect_never_invents_resolutions_when_no_video_formats(monkeypatch):
    extractor = UniversalExtractor(use_browser=False)

    monkeypatch.setattr(
        extractor,
        "_ytdlp_info",
        lambda url: {
            "title": "audio only",
            "formats": [
                {"format_id": "140", "height": None, "ext": "m4a", "vcodec": "none", "acodec": "mp4a"},
            ],
        },
    )

    result = extractor.inspect("https://example.com/audio")

    assert result["formats"] == [
        {"id": "best", "label": "الأفضل تلقائيًا", "size": None}
    ]
