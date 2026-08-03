import os
from pathlib import Path

from app.storage_lifecycle import cleanup_expired_storage, storage_stats


def test_cleanup_preserves_active_and_recent_jobs(tmp_path: Path) -> None:
    active = tmp_path / "active"
    recent = tmp_path / "recent"
    expired = tmp_path / "expired"
    for directory in (active, recent, expired):
        directory.mkdir()
        (directory / "video.mp4").write_bytes(b"video")

    os.utime(active, (900, 900))
    os.utime(recent, (980, 980))
    os.utime(expired, (800, 800))

    result = cleanup_expired_storage(
        tmp_path,
        now=1000,
        ttl_seconds=100,
        active_paths={active},
    )

    assert active.exists()
    assert recent.exists()
    assert not expired.exists()
    assert result.removed_count == 1


def test_cleanup_never_deletes_outside_storage(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = storage / "outside-link"
    link.symlink_to(outside, target_is_directory=True)

    cleanup_expired_storage(storage, now=1000, ttl_seconds=0, active_paths=set())

    assert outside.exists()
    assert (outside / "keep.txt").exists()
    assert not link.exists()


def test_storage_stats_reports_files_bytes_and_free_space(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"123")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b").write_bytes(b"45")

    stats = storage_stats(tmp_path)

    assert stats.file_count == 2
    assert stats.total_bytes == 5
    assert stats.free_bytes > 0


def test_render_startup_does_not_wipe_all_storage() -> None:
    source = Path("docker/start-render.sh").read_text(encoding="utf-8")
    assert "/app/storage/*" not in source
