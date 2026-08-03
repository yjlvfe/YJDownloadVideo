from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

_DURATION_RE = re.compile(
    r"Duration:\s*(\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)",
    re.IGNORECASE,
)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def cleanup_application_storage(
    storage_path: Path,
    *,
    tmp_root: Path = Path("/tmp"),
) -> None:
    """Delete only this application's temporary and retained media."""
    if tmp_root.exists():
        for pattern in ("yjdownload-*", "yjpreview-*"):
            for path in tmp_root.glob(pattern):
                _remove_path(path)

    storage_path.mkdir(parents=True, exist_ok=True)
    for path in storage_path.iterdir():
        _remove_path(path)


def _iter_files(path: Path):
    if not path.exists():
        return
    if path.is_file():
        yield path
        return
    for item in path.rglob("*"):
        if item.is_file():
            yield item


def retained_application_media_stats(
    storage_path: Path,
    *,
    tmp_root: Path = Path("/tmp"),
) -> tuple[int, int]:
    files: list[Path] = []
    if tmp_root.exists():
        for pattern in ("yjdownload-*", "yjpreview-*"):
            for path in tmp_root.glob(pattern):
                files.extend(_iter_files(path) or [])
    files.extend(_iter_files(storage_path) or [])
    return len(files), sum(item.stat().st_size for item in files)


def media_duration(
    path: Path,
    *,
    ffmpeg_bin: str,
    runner: Callable = subprocess.run,
) -> float:
    """Read duration from ffmpeg's input header without requiring ffprobe."""
    result = runner(
        [ffmpeg_bin, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    match = _DURATION_RE.search(str(getattr(result, "stderr", "") or ""))
    if not match:
        return 0.1
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return max(0.1, hours * 3600 + minutes * 60 + seconds)


def install_vercel_runtime_hooks(flow_module) -> None:
    """Use ffmpeg-only probing in the Vercel media flow."""
    from .config import ffmpeg_location

    flow_module._probe_duration = lambda path: media_duration(
        Path(path),
        ffmpeg_bin=ffmpeg_location(),
    )
