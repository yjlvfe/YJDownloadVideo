from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import durable_runtime
from .extractor import DownloadError, UniversalExtractor, _ytdlp_headers


_PATCHED = False
_ORIGINAL_BASE_OPTIONS = UniversalExtractor._base_ytdlp_opts
_ORIGINAL_DOWNLOAD = UniversalExtractor.download
_ORIGINAL_RETRYABLE = durable_runtime.is_retryable_job_error


def _provider_extractor_args() -> dict[str, dict[str, list[str]]]:
    provider_url = os.getenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "").strip()
    youtube_args: dict[str, list[str]] = {
        "player_client": ["mweb"],
        "fetch_pot": ["always"],
        "pot_trace": ["false"],
        "use_ad_playback_context": ["true"],
        "player_skip": ["webpage", "configs"],
    }
    if visitor_data:
        youtube_args["visitor_data"] = [visitor_data]
    return {
        "youtube": youtube_args,
        "youtubepot-bgutilhttp": {
            "base_url": [provider_url],
        },
    }


def _patched_base_options(
    self: UniversalExtractor,
    outdir: Path | str,
    preferred: str = "best",
) -> dict[str, Any]:
    # Audio is handled after a verified best-video download in _patched_download.
    effective = "best" if preferred == "audio" else preferred
    options = _ORIGINAL_BASE_OPTIONS(self, outdir, effective)
    options["extractor_args"] = _provider_extractor_args()
    options["js_runtimes"] = {"node": {}}
    return options


def _patched_download(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str = "best",
    title_hint: str = "",
) -> dict[str, Any]:
    if preferred != "audio":
        return _ORIGINAL_DOWNLOAD(self, url, outdir, preferred, title_hint)
    # Use the exact path already proven by the video button, then extract audio
    # from that complete media file. This avoids broken platform audio-only URLs.
    video_result = _ORIGINAL_DOWNLOAD(self, url, outdir, "best", title_hint)
    return self._convert_to_audio_only(video_result, Path(outdir))


def _patched_ytdlp_info(self: UniversalExtractor, url: str) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    options: dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "no_warnings": True,
        "socket_timeout": 45,
        "http_headers": _ytdlp_headers(),
        "extractor_args": _provider_extractor_args(),
        "js_runtimes": {"node": {}},
    }
    if self.cookies_file:
        options["cookiefile"] = self.cookies_file
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise DownloadError("yt-dlp لم يُرجع بيانات")
    if info.get("is_live"):
        raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
    return info


def _patched_retryable(message: str) -> bool:
    lowered = str(message or "").lower()
    terminal_markers = (
        "http error 403",
        "403 forbidden",
        "unable to obtain file audio codec",
        "postprocessing:",
    )
    if any(marker in lowered for marker in terminal_markers):
        return False
    return _ORIGINAL_RETRYABLE(message)


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    UniversalExtractor._base_ytdlp_opts = _patched_base_options
    UniversalExtractor.download = _patched_download
    UniversalExtractor._ytdlp_info = _patched_ytdlp_info
    durable_runtime.is_retryable_job_error = _patched_retryable
    _PATCHED = True


install()
