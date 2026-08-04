from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CleanupResult:
    removed_count: int
    removed_bytes: int


@dataclass(frozen=True, slots=True)
class StorageStats:
    file_count: int
    total_bytes: int
    free_bytes: int


def _contained(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _entry_bytes(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    return total


def cleanup_expired_storage(
    root: str | Path,
    *,
    now: float,
    ttl_seconds: int,
    active_paths: Iterable[str | Path],
    temporary_grace_seconds: int = 3 * 60 * 60,
) -> CleanupResult:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    active = {
        Path(path).resolve(strict=False)
        for path in active_paths
        if _contained(root_path, Path(path))
    }
    removed_count = 0
    removed_bytes = 0
    ttl = max(0, int(ttl_seconds))
    temporary_grace = max(ttl, int(temporary_grace_seconds))

    for entry in root_path.iterdir():
        if entry.resolve(strict=False) in active:
            continue
        if entry.is_symlink():
            entry.unlink(missing_ok=True)
            removed_count += 1
            continue
        if not _contained(root_path, entry):
            continue
        try:
            age = float(now) - entry.stat().st_mtime
        except OSError:
            continue

        required_age = temporary_grace if entry.name.startswith("yjdownload-") else ttl
        if age < required_age:
            continue

        removed_bytes += _entry_bytes(entry)
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed_count += 1

    return CleanupResult(removed_count=removed_count, removed_bytes=removed_bytes)


def storage_stats(root: str | Path) -> StorageStats:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    for path in root_path.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        file_count += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
    usage = shutil.disk_usage(root_path)
    return StorageStats(
        file_count=file_count,
        total_bytes=total_bytes,
        free_bytes=int(usage.free),
    )
