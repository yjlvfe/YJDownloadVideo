from __future__ import annotations

from pathlib import Path
from typing import Any

from .extractor import DownloadError, UniversalExtractor, _has_audio, _has_video
from .youtube_innertube import YouTubeInnertubeError, run_youtube_innertube


_PATCHED = False
_ORIGINAL_INSPECT = UniversalExtractor.inspect
_ORIGINAL_DOWNLOAD = UniversalExtractor.download


def _eligible(extractor: UniversalExtractor, url: str) -> bool:
    return bool(extractor._is_youtube(url) and not extractor.cookies_file)


def _inspect_with_innertube(extractor: UniversalExtractor, url: str) -> dict[str, Any]:
    try:
        result = run_youtube_innertube(
            action="inspect",
            url=url,
            max_bytes=extractor.max_filesize,
            timeout_seconds=max(90, extractor.request_timeout * 6),
        )
    except YouTubeInnertubeError as exc:
        raise DownloadError(str(exc)) from exc
    formats = result.get("formats") or []
    if not formats:
        raise DownloadError("YouTube.js لم يُرجع جودات قابلة للتنزيل")
    return result


def _download_with_innertube(
    extractor: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
) -> dict[str, Any]:
    try:
        result = run_youtube_innertube(
            action="download",
            url=url,
            preferred=preferred,
            output_dir=Path(outdir),
            max_bytes=extractor.max_filesize,
            timeout_seconds=900,
        )
    except YouTubeInnertubeError as exc:
        raise DownloadError(str(exc)) from exc

    path = Path(str(result.get("path") or ""))
    if not path.is_file():
        raise DownloadError("YouTube.js لم ينشئ الملف")
    if not _has_audio(path):
        path.unlink(missing_ok=True)
        raise DownloadError("YouTube.js output has no audio")
    if preferred != "audio" and not _has_video(path):
        path.unlink(missing_ok=True)
        raise DownloadError("YouTube.js output has no video")
    return {
        "path": str(path),
        "title": str(result.get("title") or ""),
        "duration": result.get("duration"),
        "thumbnail": str(result.get("thumbnail") or ""),
        "ext": str(result.get("ext") or path.suffix.lstrip(".")),
    }


def _patched_inspect(self: UniversalExtractor, url: str) -> dict[str, Any]:
    if _eligible(self, url):
        try:
            return _inspect_with_innertube(self, url)
        except DownloadError:
            pass
    return _ORIGINAL_INSPECT(self, url)


def _patched_download(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str = "best",
    title_hint: str = "",
) -> dict[str, Any]:
    if _eligible(self, url):
        try:
            return _download_with_innertube(self, url, Path(outdir), preferred)
        except DownloadError:
            pass
    return _ORIGINAL_DOWNLOAD(self, url, outdir, preferred, title_hint)


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    UniversalExtractor.inspect = _patched_inspect
    UniversalExtractor.download = _patched_download
    _PATCHED = True


install()
