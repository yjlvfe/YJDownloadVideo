from __future__ import annotations

import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class QualityOption:
    key: str
    label: str
    selector: str
    size: int | None = None


def _height_of(item: dict[str, Any]) -> int | None:
    raw = item.get("height")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    label = str(item.get("label") or "")
    match = re.search(r"(?<!\d)(\d{2,4})p(?!\d)", label, re.IGNORECASE)
    return int(match.group(1)) if match else None


def normalize_qualities(raw_formats: list[dict[str, Any]] | None) -> list[QualityOption]:
    by_height: dict[int, QualityOption] = {}
    for item in raw_formats or []:
        if item.get("vcodec") == "none":
            continue
        height = _height_of(item)
        if not height:
            continue
        size = item.get("filesize") or item.get("filesize_approx") or item.get("size")
        size = int(size) if isinstance(size, (int, float)) and size > 0 else None
        candidate = QualityOption(
            key=str(height),
            label=f"{height}p",
            selector=(
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]"
            ),
            size=size,
        )
        previous = by_height.get(height)
        if previous is None or (candidate.size or 0) > (previous.size or 0):
            by_height[height] = candidate

    if not by_height:
        return [
            QualityOption(
                key="original",
                label="الجودة الأصلية",
                selector="bestvideo*+bestaudio/best",
                size=None,
            )
        ]
    return [by_height[key] for key in sorted(by_height)]


@dataclass(slots=True)
class _Selection:
    owner_id: int
    value: dict[str, Any]
    expires_at: float


class SelectionStore:
    def __init__(
        self,
        ttl_seconds: int = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.clock = clock
        self._items: dict[str, _Selection] = {}

    def create(self, owner_id: int, value: dict[str, Any]) -> str:
        self.cleanup()
        token = secrets.token_urlsafe(8)
        self._items[token] = _Selection(
            owner_id=int(owner_id),
            value=dict(value),
            expires_at=self.clock() + self.ttl_seconds,
        )
        return token

    def get(self, token: str, owner_id: int) -> dict[str, Any] | None:
        item = self._items.get(token)
        if item is None:
            return None
        if item.expires_at <= self.clock():
            self._items.pop(token, None)
            return None
        if item.owner_id != int(owner_id):
            return None
        return item.value

    def pop(self, token: str, owner_id: int) -> dict[str, Any] | None:
        value = self.get(token, owner_id)
        if value is not None:
            self._items.pop(token, None)
        return value

    def cleanup(self) -> int:
        now = self.clock()
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            self._items.pop(token, None)
        return len(expired)

    @property
    def count(self) -> int:
        self.cleanup()
        return len(self._items)


def progress_step(pct: int | float) -> int:
    bounded = max(0, min(100, int(pct)))
    return (bounded // 10) * 10


def safe_media_title(title: str, suffix: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", str(title or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(". ") or "media"
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    max_stem = max(1, 180 - len(suffix))
    return f"{cleaned[:max_stem].rstrip()}{suffix}"


def cleanup_path(path: str | Path) -> None:
    target = Path(path)
    if not target.exists():
        return
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink(missing_ok=True)
