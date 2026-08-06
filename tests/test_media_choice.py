from pathlib import Path

from app.media_choice import (
    SelectionStore,
    cleanup_path,
    is_quality_key,
    normalize_qualities,
    progress_step,
    safe_media_title,
    selector_for_quality,
)


def test_normalize_qualities_returns_only_real_unique_heights():
    formats = [
        {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "filesize": 10},
        {"format_id": "22", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "filesize": 20},
        {"format_id": "136", "height": 720, "vcodec": "avc1", "acodec": "none", "filesize": 30},
        {"format_id": "140", "height": None, "vcodec": "none", "acodec": "mp4a", "filesize": 5},
    ]

    qualities = normalize_qualities(formats)

    assert [item.key for item in qualities] == ["360", "720"]
    assert qualities[1].selector == (
        "bestvideo[height=720]+bestaudio/best[height=720]/"
        "bestvideo[height<=720]+bestaudio/best[height<=720]"
    )
    assert qualities[1].size == 35


def test_normalize_qualities_supports_4k_8k_and_prefers_compatible_video():
    formats = [
        {"format_id": "sb0", "height": 180, "vcodec": "none", "acodec": "none", "filesize": 1},
        {"format_id": "140", "height": None, "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize": 5},
        {"format_id": "22", "height": 720, "ext": "mp4", "vcodec": "avc1.64001F", "acodec": "mp4a", "filesize": 30},
        {"format_id": "313", "height": 2160, "ext": "webm", "vcodec": "vp9", "acodec": "none", "filesize": 100},
        {"format_id": "137", "height": 2160, "ext": "mp4", "vcodec": "avc1.640033", "acodec": "none", "filesize": 80},
        {"format_id": "702", "height": 4320, "ext": "mp4", "vcodec": "av01.0.16M.08", "acodec": "none", "filesize": 200},
    ]

    qualities = normalize_qualities(formats)

    assert [item.key for item in qualities] == ["720", "2160", "4320"]
    assert qualities[0].size == 30
    assert qualities[1].size == 85
    assert qualities[2].size == 205
    assert qualities[1].selector.startswith("bestvideo[height=2160]+bestaudio")
    assert selector_for_quality("4320").startswith("bestvideo[height=4320]+bestaudio")
    assert is_quality_key("4320") is True


def test_normalize_qualities_never_invents_resolutions():
    assert [item.key for item in normalize_qualities([])] == ["original"]


def test_selection_store_enforces_owner_and_expiry():
    now = [100.0]
    store = SelectionStore(ttl_seconds=15, clock=lambda: now[0])
    token = store.create(owner_id=7, value={"url": "https://example.com/v.mp4"})

    assert store.get(token, owner_id=7) is not None
    assert store.get(token, owner_id=8) is None

    now[0] = 116.0
    assert store.get(token, owner_id=7) is None


def test_progress_step_updates_only_on_ten_percent_boundaries():
    assert progress_step(0) == 0
    assert progress_step(9) == 0
    assert progress_step(10) == 10
    assert progress_step(99) == 90
    assert progress_step(100) == 100
    assert progress_step(140) == 100


def test_safe_media_title_keeps_arabic_and_removes_path_characters():
    assert safe_media_title('  اسم/المقطع: الأول?  ', '.mp3') == 'اسم المقطع الأول.mp3'


def test_cleanup_path_removes_entire_job_directory(tmp_path: Path):
    job = tmp_path / "yjdownload-job"
    job.mkdir()
    (job / "video.mp4").write_bytes(b"x")
    nested = job / "parts"
    nested.mkdir()
    (nested / "1.part").write_bytes(b"y")

    cleanup_path(job)

    assert not job.exists()
