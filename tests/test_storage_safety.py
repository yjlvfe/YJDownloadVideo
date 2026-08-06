import json
import os
from pathlib import Path

import pytest

from app.storage_leases import StorageLeaseRegistry
from app.storage_lifecycle import cleanup_expired_storage, storage_stats


def test_live_lease_protects_expired_download_directory(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    download = storage / "job-1"
    download.mkdir(parents=True)
    (download / "video.mp4").write_bytes(b"video")
    os.utime(download, (100, 100))

    registry = StorageLeaseRegistry(storage, stale_after_seconds=300)
    with registry.lease(download, now=950):
        result = cleanup_expired_storage(
            storage,
            now=1000,
            ttl_seconds=100,
            active_paths=registry.active_paths(now=1000),
        )

    assert download.exists()
    assert result.removed_count == 0


def test_cleanup_discovers_live_leases_without_caller_bookkeeping(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    download = storage / "job-auto"
    download.mkdir(parents=True)
    (download / "video.mp4").write_bytes(b"video")
    os.utime(download, (100, 100))

    registry = StorageLeaseRegistry(storage, stale_after_seconds=300)
    with registry.lease(download, now=950):
        result = cleanup_expired_storage(
            storage,
            now=1000,
            ttl_seconds=100,
            active_paths=set(),
        )

    assert download.exists()
    assert result.removed_count == 0


def test_cleanup_preserves_recent_and_removes_expired_unleased_jobs(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    recent = storage / "recent"
    expired = storage / "expired"
    for directory in (recent, expired):
        directory.mkdir(parents=True)
        (directory / "video.mp4").write_bytes(b"video")
    os.utime(recent, (980, 980))
    os.utime(expired, (800, 800))

    result = cleanup_expired_storage(
        storage,
        now=1000,
        ttl_seconds=100,
        active_paths=set(),
    )

    assert recent.exists()
    assert not expired.exists()
    assert result.removed_count == 1


def test_released_lease_allows_expired_directory_cleanup(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    download = storage / "job-2"
    download.mkdir(parents=True)
    (download / "video.mp4").write_bytes(b"video")
    os.utime(download, (100, 100))

    registry = StorageLeaseRegistry(storage, stale_after_seconds=300)
    lease = registry.lease(download, now=950)
    lease.close()

    result = cleanup_expired_storage(
        storage,
        now=1000,
        ttl_seconds=100,
        active_paths=registry.active_paths(now=1000),
    )

    assert not download.exists()
    assert result.removed_count == 1


def test_temporary_media_uses_extended_orphan_grace(tmp_path: Path) -> None:
    temporary = tmp_path / "yjdownload-active"
    temporary.mkdir()
    (temporary / "video.part").write_bytes(b"video")
    os.utime(temporary, (1000, 1000))

    cleanup_expired_storage(
        tmp_path,
        now=5000,
        ttl_seconds=3600,
        temporary_grace_seconds=7200,
        active_paths=set(),
    )
    assert temporary.exists()

    cleanup_expired_storage(
        tmp_path,
        now=9000,
        ttl_seconds=3600,
        temporary_grace_seconds=7200,
        active_paths=set(),
    )
    assert not temporary.exists()


def test_stale_lease_is_removed_and_does_not_protect_orphan(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    download = storage / "job-3"
    download.mkdir(parents=True)
    (download / "video.mp4").write_bytes(b"video")
    os.utime(download, (100, 100))

    registry = StorageLeaseRegistry(storage, stale_after_seconds=30)
    registry.lease(download, now=900)

    assert registry.active_paths(now=1000) == set()
    assert registry.clear_stale(now=1000) == 1

    cleanup_expired_storage(
        storage,
        now=1000,
        ttl_seconds=100,
        active_paths=set(),
    )
    assert not download.exists()


def test_lease_rejects_paths_outside_storage(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    registry = StorageLeaseRegistry(storage)

    with pytest.raises(ValueError, match="outside storage"):
        registry.lease(outside)


def test_cleanup_unlinks_symlink_without_following_external_target(tmp_path: Path) -> None:
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


def test_lease_heartbeat_is_atomic_and_contains_only_relative_path(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    download = storage / "nested" / "job"
    download.mkdir(parents=True)
    registry = StorageLeaseRegistry(storage, stale_after_seconds=300)

    lease = registry.lease(download, now=100)
    lease.heartbeat(now=200)

    files = list((storage / ".leases").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload == {"heartbeat": 200.0, "path": "nested/job"}
    assert not list((storage / ".leases").glob("*.tmp"))


def test_cleanup_never_removes_internal_lease_registry(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    target = storage / "job"
    target.mkdir(parents=True)
    registry = StorageLeaseRegistry(storage)
    lease = registry.lease(target, now=100)

    cleanup_expired_storage(
        storage,
        now=1000,
        ttl_seconds=0,
        active_paths=registry.active_paths(now=100),
    )

    assert (storage / ".leases").is_dir()
    lease.close()


def test_storage_stats_excludes_lease_metadata(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"123")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b").write_bytes(b"45")
    leased = tmp_path / "leased"
    leased.mkdir()
    lease = StorageLeaseRegistry(tmp_path).lease(leased, now=100)

    stats = storage_stats(tmp_path)

    assert stats.file_count == 2
    assert stats.total_bytes == 5
    assert stats.free_bytes > 0
    lease.close()


def test_render_startup_does_not_wipe_all_storage() -> None:
    source = Path("docker/start-render.sh").read_text(encoding="utf-8")
    assert "/app/storage/*" not in source
