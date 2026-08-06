from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractor import UniversalExtractor
from .media_choice import is_quality_key, selector_for_quality


_PATCHED = False
_ORIGINAL_INSPECT = UniversalExtractor.inspect
_ORIGINAL_BASE_OPTIONS = UniversalExtractor._base_ytdlp_opts


def _real_format_records(info: dict[str, Any], max_filesize: int = 0) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in info.get("formats") or []:
        if str(item.get("vcodec") or "").lower() in {"", "none"}:
            continue
        height = item.get("height")
        if not isinstance(height, (int, float)) or int(height) <= 0:
            continue
        size = item.get("filesize") or item.get("filesize_approx")
        if max_filesize and isinstance(size, (int, float)) and size > max_filesize:
            continue
        records.append(
            {
                "id": str(item.get("format_id") or int(height)),
                "height": int(height),
                "ext": str(item.get("ext") or ""),
                "vcodec": str(item.get("vcodec") or ""),
                "acodec": str(item.get("acodec") or ""),
                "filesize": item.get("filesize"),
                "filesize_approx": item.get("filesize_approx"),
            }
        )
    return records


def _patched_inspect(self: UniversalExtractor, url: str) -> dict[str, Any]:
    try:
        info = self._ytdlp_info(url)
    except Exception:  # noqa: BLE001
        return _ORIGINAL_INSPECT(self, url)

    formats = _real_format_records(info, int(self.max_filesize or 0))
    if not formats:
        formats = [{"id": "best", "label": "الأفضل تلقائيًا", "size": None}]
    return {
        "source": "ytdlp",
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "formats": formats,
    }


def _patched_base_options(
    self: UniversalExtractor,
    outdir: Path | str,
    preferred: str = "best",
) -> dict[str, Any]:
    options = _ORIGINAL_BASE_OPTIONS(self, outdir, preferred)
    normalized = str(preferred or "best").strip().lower()
    if is_quality_key(normalized, include_audio=True):
        options["format"] = selector_for_quality(normalized)
        if normalized == "audio":
            options["merge_output_format"] = "m4a"
    return options


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    UniversalExtractor.inspect = _patched_inspect
    UniversalExtractor._base_ytdlp_opts = _patched_base_options
    _PATCHED = True
