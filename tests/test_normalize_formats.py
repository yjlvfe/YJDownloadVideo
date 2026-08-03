"""اختبارات قائمة الجودات الموحدة (normalize_formats)."""
from app.main import normalize_formats


def test_normalize_dedupes_and_sorts():
    raw = [
        {"id": "137", "label": "1080p / mp4", "size": 100},
        {"id": "18", "label": "360p / mp4", "size": 10},
        {"id": "22", "label": "720p / mp4", "size": 50},
        {"id": "136", "label": "720p / webm", "size": 40},
    ]
    out = normalize_formats(raw)
    ids = [f["id"] for f in out]
    assert ids[0] == "best"
    assert "1080" in ids and "720" in ids and "360" in ids
    assert ids[-1] == "audio"
    # لكل دقة/امتداد خيار واحد فقط
    labels = [f["label"] for f in out if f["id"] != "best" and f["id"] != "audio"]
    assert len(labels) == len(set(labels))


def test_normalize_keeps_largest_size_per_key():
    raw = [
        {"id": "a", "label": "720p / mp4", "size": 30},
        {"id": "b", "label": "720p / mp4", "size": 60},
    ]
    out = normalize_formats(raw)
    item = next(f for f in out if f["label"] == "720p / mp4")
    assert item["size"] == 60


def test_normalize_fallback_presets():
    out = normalize_formats([])
    ids = [f["id"] for f in out]
    assert ids == ["best", "1080", "720", "480", "audio"]
