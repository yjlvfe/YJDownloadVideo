import json
import os
from pathlib import Path

import pytest

from app.storage_leases import StorageLeaseRegistry
from app.storage_lifecycle import cleanup_expired_storage


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
