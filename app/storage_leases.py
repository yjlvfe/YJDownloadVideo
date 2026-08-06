from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


LEASE_DIRECTORY_NAME = ".leases"


def _resolved(path: str | Path) -> Path:
    return Path(path).resolve(strict=False)


@dataclass(slots=True)
class StorageLease:
    registry: "StorageLeaseRegistry"
    path: Path
    lease_file: Path
    closed: bool = False

    def heartbeat(self, *, now: float | None = None) -> None:
        if self.closed:
            raise RuntimeError("storage lease is closed")
        self.registry._write(self.path, self.lease_file, now=now)

    def close(self) -> None:
        if self.closed:
            return
        self.lease_file.unlink(missing_ok=True)
        self.closed = True

    def __enter__(self) -> "StorageLease":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class StorageLeaseRegistry:
    def __init__(
        self,
        root: str | Path,
        stale_after_seconds: int = 15 * 60,
    ) -> None:
        self.root = _resolved(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root / LEASE_DIRECTORY_NAME
        self.directory.mkdir(parents=True, exist_ok=True)
        self.stale_after_seconds = max(1, int(stale_after_seconds))

    def _relative(self, path: str | Path) -> Path:
        candidate = _resolved(path)
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path is outside storage") from exc
        if relative == Path(".") or relative.parts[0] == LEASE_DIRECTORY_NAME:
            raise ValueError("path is outside storage")
        return relative

    def _lease_file(self, relative: Path) -> Path:
        digest = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def _write(
        self,
        path: str | Path,
        lease_file: Path,
        *,
        now: float | None = None,
    ) -> None:
        relative = self._relative(path)
        payload = {
            "heartbeat": float(time.time() if now is None else now),
            "path": relative.as_posix(),
        }
        temporary = lease_file.with_suffix(
            f"{lease_file.suffix}.{os.getpid()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(lease_file)
        os.chmod(lease_file, 0o600)

    def lease(
        self,
        path: str | Path,
        *,
        now: float | None = None,
    ) -> StorageLease:
        relative = self._relative(path)
        target = self.root / relative
        lease_file = self._lease_file(relative)
        lease = StorageLease(self, target, lease_file)
        lease.heartbeat(now=now)
        return lease

    def _read(self, lease_file: Path) -> tuple[Path, float] | None:
        try:
            payload = json.loads(lease_file.read_text(encoding="utf-8"))
            relative = Path(str(payload["path"]))
            heartbeat = float(payload["heartbeat"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if relative.is_absolute() or ".." in relative.parts:
            return None
        candidate = _resolved(self.root / relative)
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate, heartbeat

    def active_paths(self, *, now: float | None = None) -> set[Path]:
        current = float(time.time() if now is None else now)
        active: set[Path] = set()
        for lease_file in self.directory.glob("*.json"):
            record = self._read(lease_file)
            if record is None:
                continue
            path, heartbeat = record
            if current - heartbeat <= self.stale_after_seconds:
                active.add(path)
        return active

    def clear_stale(self, *, now: float | None = None) -> int:
        current = float(time.time() if now is None else now)
        removed = 0
        for lease_file in self.directory.glob("*.json"):
            record = self._read(lease_file)
            if record is None or current - record[1] > self.stale_after_seconds:
                lease_file.unlink(missing_ok=True)
                removed += 1
        for temporary in self.directory.glob("*.tmp"):
            temporary.unlink(missing_ok=True)
        return removed
