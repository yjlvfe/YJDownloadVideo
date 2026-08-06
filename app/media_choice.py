from __future__ import annotations

import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


MIN_QUALITY_HEIGHT = 144
MAX_QUALITY_HEIGHT = 8192


@dataclass(frozen=True, slots=True)
class QualityOption:
    key: str
    label: str
    selector: str
    size: int | None = None


def _bounded_video_selector(height: int) -> str:
    return (
        f"bestvideo[height={height}]+bestaudio/"
        f"best[height={height}]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]"
    )


_QUALITY_REGISTRY: tuple[QualityOption, ...] = (
    QualityOption(
        key="best",
        label="⭐ الأفضل",
        selector="bestvideo*+bestaudio/best",
    ),
    QualityOption(
        key="audio",
        label="🎧 صوت MP3",
        selector="bestaudio/best",
    ),
)
_QUALITY_BY_KEY = {item.key: item for item in _QUALITY_REGISTRY}
_QUALITY_KEY_BY_SELECTOR = {item.selector: item.key for item in _QUALITY_REGISTRY}
_DYNAMIC_SELECTOR_RE = re.compile(
    r"^bestvideo\[height=(\d{2,4})\]\+bestaudio/"
    r"best\[height=\1\]/bestvideo\[height<=\1\]\+bestaudio/"
    r"best\[height<=\1\]$"
)


def _valid_height(value: int) -> bool:
    return MIN_QUALITY_HEIGHT <= int(value) <= MAX_QUALITY_HEIGHT


def quality_options(*, include_audio: bool = False) -> list[QualityOption]:
    return [
        item
        for item in _QUALITY_REGISTRY
        if include_audio or item.key != "audio"
    ]


def selector_for_quality(key: str) -> str:
    normalized = str(key or "").strip().lower()
    option = _QUALITY_BY_KEY.get(normalized)
    if option is not None:
        return option.selector
    if normalized == "original":
        return _QUALITY_BY_KEY["best"].selector
    if normalized.isdigit():
        height = int(normalized)
        if _valid_height(height):
            return _bounded_video_selector(height)
    raise ValueError("unknown quality")


def quality_key_for_selector(selector: str) -> str | None:
    normalized = str(selector or "")
    static = _QUALITY_KEY_BY_SELECTOR.get(normalized)
    if static is not None:
        return static
    match = _DYNAMIC_SELECTOR_RE.fullmatch(normalized)
    if match:
        height = int(match.group(1))
        if _valid_height(height):
            return str(height)
    return None


def is_quality_key(key: str, *, include_audio: bool = False) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in {
        item.key for item in quality_options(include_audio=include_audio)
    }:
        return True
    if normalized == "original":
        return True
    return normalized.isdigit() and _valid_height(int(normalized))


def _height_of(item: dict[str, Any]) -> int | None:
    raw = item.get("height")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    label = str(item.get("label") or "")
    match = re.search(r"(?<!\d)(\d{2,4})p(?!\d)", label, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _format_size(item: dict[str, Any]) -> int | None:
    raw = item.get("filesize") or item.get("filesize_approx") or item.get("size")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    return None


def _compatibility_score(item: dict[str, Any]) -> int:
    ext = str(item.get("ext") or "").lower()
    codec = str(item.get("vcodec") or "").lower()
    if ext == "mp4" and codec.startswith("avc1"):
        return 0
    if ext == "mp4":
        return 1
    return 2


def normalize_qualities(raw_formats: list[dict[str, Any]] | None) -> list[QualityOption]:
    formats = list(raw_formats or [])
    audio_sizes = [
        size
        for item in formats
        if str(item.get("vcodec") or "").lower() == "none"
        and str(item.get("acodec") or "").lower() not in {"", "none"}
        and (size := _format_size(item)) is not None
    ]
    best_audio_size = max(audio_sizes, default=0)

    by_height: dict[int, tuple[int, int, QualityOption]] = {}
    for item in formats:
        if str(item.get("vcodec") or "").lower() in {"", "none"}:
            continue
        height = _height_of(item)
        if not height or not _valid_height(height):
            continue

        video_size = _format_size(item)
        has_audio = str(item.get("acodec") or "").lower() not in {"", "none"}
        final_size = video_size
        if video_size is not None and not has_audio and best_audio_size:
            final_size = video_size + best_audio_size

        score = _compatibility_score(item)
        candidate = QualityOption(
            key=str(height),
            label=f"{height}p",
            selector=_bounded_video_selector(height),
            size=final_size,
        )
        previous = by_height.get(height)
        candidate_base_size = video_size or 0
        if (
            previous is None
            or score < previous[0]
            or (score == previous[0] and candidate_base_size > previous[1])
        ):
            by_height[height] = (score, candidate_base_size, candidate)

    if not by_height:
        return [
            QualityOption(
                key="original",
                label="الجودة الأصلية",
                selector=selector_for_quality("best"),
                size=None,
            )
        ]
    return [by_height[key][2] for key in sorted(by_height)]


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