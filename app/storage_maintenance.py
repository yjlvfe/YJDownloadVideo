from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .db import SessionLocal
from .job_expiration import ExpirationResult, reconcile_expired_jobs
from .storage_lifecycle import CleanupResult, cleanup_expired_storage


@dataclass(frozen=True, slots=True)
class StorageMaintenanceResult:
    cleanup: CleanupResult
    expiration: ExpirationResult


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def run_storage_maintenance(
    *,
    storage_root: str | Path,
    now: datetime,
    ttl_seconds: int,
    session_factory: Callable[[], Any] = SessionLocal,
) -> StorageMaintenanceResult:
    current = _utc(now)
    expiration = reconcile_expired_jobs(
        now=current,
        storage_root=storage_root,
        session_factory=session_factory,
    )
    cleanup = cleanup_expired_storage(
        storage_root,
        now=current.timestamp(),
        ttl_seconds=ttl_seconds,
        active_paths=set(),
    )
    return StorageMaintenanceResult(
        cleanup=cleanup,
        expiration=expiration,
    )
