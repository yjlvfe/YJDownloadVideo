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


def test_normalize_empty_has_no_fake_qualities():
    """لا جودات وهمية أبدًا — بدون جودات حقيقية يبقى فقط الأفضل والصوت."""
    out = normalize_formats([])
    ids = [f["id"] for f in out]
    assert ids == ["best", "audio"]


def test_normalize_no_duplicate_ids():
    """دقة واحدة لكل ارتفاع ولو تعددت الامتدادات — كان يعطي زرين بنفس format_id."""
    out = normalize_formats([
        {"id": "137", "label": "1080p / mp4", "size": 100},
        {"id": "248", "label": "1080p / webm", "size": 90},
        {"id": "18", "label": "360p / mp4", "size": 10},
    ])
    ids = [f["id"] for f in out]
    assert ids == ["best", "1080", "360", "audio"]
    assert ids.count("1080") == 1


def test_normalize_accepts_more_label_shapes():
    """صيغ إضافية: 1080p60 / 2160p / بلا امتداد."""
    out = normalize_formats([
        {"id": "a", "label": "1080p60", "size": 10},
        {"id": "b", "label": "2160p", "size": 20},
    ])
    ids = [f["id"] for f in out]
    assert "1080" in ids and "2160" in ids
