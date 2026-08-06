from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .db import Job, SessionLocal


@dataclass(frozen=True, slots=True)
class ExpirationResult:
    expired_count: int = 0
    removed_count: int = 0
    removed_bytes: int = 0

    def __add__(self, other: "ExpirationResult") -> "ExpirationResult":
        return ExpirationResult(
            expired_count=self.expired_count + other.expired_count,
            removed_count=self.removed_count + other.removed_count,
            removed_bytes=self.removed_bytes + other.removed_bytes,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _entry_bytes(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    if path.is_dir():
        for child in path.rglob("*"):
            if not child.is_file() or child.is_symlink():
                continue
            try:
                total += int(child.stat().st_size)
            except OSError:
                pass
    return total


def _artifact_root(storage_root: str | Path, file_path: str | Path) -> Path | None:
    root = Path(storage_root).resolve(strict=False)
    candidate = Path(file_path).resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] == ".leases":
        return None
    target = root / relative.parts[0]
    try:
        target.resolve(strict=False).relative_to(root)
    except ValueError:
        return None
    return target


def _remove_artifact_tree(
    storage_root: str | Path,
    file_path: str | Path | None,
) -> tuple[int, int]:
    if not file_path:
        return 0, 0
    target = _artifact_root(storage_root, file_path)
    if target is None or not target.exists():
        return 0, 0
    removed_bytes = _entry_bytes(target)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink(missing_ok=True)
    return 1, removed_bytes


def _expire_record(job: Job, *, storage_root: str | Path) -> ExpirationResult:
    removed_count, removed_bytes = _remove_artifact_tree(
        storage_root,
        job.file_path,
    )
    changed = bool(
        job.status != "expired"
        or job.file_path
        or job.download_token
        or job.file_size is not None
    )
    job.status = "expired"
    job.file_path = None
    job.download_token = None
    job.file_size = None
    job.expires_at = None
    return ExpirationResult(
        expired_count=1 if changed else 0,
        removed_count=removed_count,
        removed_bytes=removed_bytes,
    )


def expire_job_artifacts(
    job_id: str,
    *,
    storage_root: str | Path,
    session_factory: Callable[[], Any] = SessionLocal,
) -> ExpirationResult:
    with session_factory() as database:
        job = database.get(Job, str(job_id))
        if job is None:
            return ExpirationResult()
        result = _expire_record(job, storage_root=storage_root)
        database.commit()
        return result


def reconcile_expired_jobs(
    *,
    now: datetime,
    storage_root: str | Path,
    session_factory: Callable[[], Any] = SessionLocal,
) -> ExpirationResult:
    current = _utc(now)
    total = ExpirationResult()
    with session_factory() as database:
        jobs = database.query(Job).filter(Job.file_path.is_not(None)).all()
        for job in jobs:
            file_path = Path(str(job.file_path))
            elapsed = bool(
                job.expires_at is not None
                and _utc(job.expires_at) <= current
            )
            missing = not file_path.is_file()
            if not elapsed and not missing:
                continue
            total += _expire_record(job, storage_root=storage_root)
        database.commit()
    return total
