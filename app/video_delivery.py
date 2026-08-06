from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ffmpeg_location, ffprobe_location


@dataclass(frozen=True, slots=True)
class VideoUploadMetadata:
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    thumbnail: Path | None = None


def _positive_int(value) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _probe_video(path: Path) -> tuple[int | None, int | None, int | None]:
    try:
        result = subprocess.run(
            [
                ffprobe_location(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,duration:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            return None, None, None
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        stream = streams[0] if streams and isinstance(streams[0], dict) else {}
        duration = _positive_int(stream.get("duration")) or _positive_int(
            (payload.get("format") or {}).get("duration")
        )
        return (
            duration,
            _positive_int(stream.get("width")),
            _positive_int(stream.get("height")),
        )
    except Exception:  # noqa: BLE001
        return None, None, None


def _make_thumbnail(path: Path, target: Path, duration: int | None) -> Path | None:
    seek_at = min(30, max(1, duration // 4)) if duration else 2
    try:
        target.unlink(missing_ok=True)
        result = subprocess.run(
            [
                ffmpeg_location(),
                "-y",
                "-v",
                "error",
                "-ss",
                str(seek_at),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(320,iw)':-2",
                "-q:v",
                "5",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            target.unlink(missing_ok=True)
            return None
        if not target.is_file() or target.stat().st_size <= 0:
            return None
        if target.stat().st_size > 200_000:
            target.unlink(missing_ok=True)
            return None
        return target
    except Exception:  # noqa: BLE001
        target.unlink(missing_ok=True)
        return None


def prepare_video_upload(
    path: str | Path,
    workdir: str | Path | None = None,
) -> VideoUploadMetadata:
    video = Path(path)
    duration, width, height = _probe_video(video)
    directory = Path(workdir) if workdir is not None else video.parent
    directory.mkdir(parents=True, exist_ok=True)
    thumbnail = _make_thumbnail(
        video,
        directory / f".{video.stem}.telegram-thumb.jpg",
        duration,
    )
    return VideoUploadMetadata(
        duration=duration,
        width=width,
        height=height,
        thumbnail=thumbnail,
    )
