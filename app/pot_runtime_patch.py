from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import durable_runtime
from . import extractor as extractor_module
from .extractor import COMBINED_ITAGS, DownloadError, UniversalExtractor, _ytdlp_headers


_PATCHED = False
_ORIGINAL_BASE_OPTIONS = UniversalExtractor._base_ytdlp_opts
_ORIGINAL_DOWNLOAD = UniversalExtractor.download
_ORIGINAL_RETRYABLE = durable_runtime.is_retryable_job_error


def _provider_extractor_args() -> dict[str, dict[str, list[str]]]:
    provider_url = os.getenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "").strip()
    youtube_args: dict[str, list[str]] = {
        # Keep yt-dlp's maintained default client set first (currently
        # android_vr + web_safari), then retain mweb + PO token as fallback.
        "player_client": ["default", "mweb"],
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


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def _candidate_itag(url: str) -> int:
    try:
        value = (parse_qs(urlparse(str(url)).query).get("itag") or ["0"])[0]
        return int(value)
    except (TypeError, ValueError):
        return 0


def _live_cdp_candidates(discovery: dict[str, Any], preferred: str) -> list[Any]:
    # Source identity is the trust boundary. CDP/CDP-DOM means Chromium saw the
    # request live in its own session. HTML/JS candidates (including YouTube
    # storyboards) remain excluded regardless of their host or file extension.
    candidates = [
        candidate
        for candidate in (discovery.get("candidates") or [])
        if str(getattr(candidate, "source", "") or "").lower() in {"cdp", "cdp-dom"}
    ]
    target = int(preferred) if str(preferred).isdigit() else None

    def rank(candidate: Any) -> tuple[int, int, int, int, int]:
        height = int(getattr(candidate, "height", 0) or 0)
        combined = int(_candidate_itag(str(candidate.url)) in COMBINED_ITAGS)
        score = int(getattr(candidate, "score", 0) or 0)
        if target is None:
            return combined, int(height > 0), height, score, 0
        exact = int(height == target)
        under = int(0 < height <= target)
        distance = -abs(height - target) if height else -100_000
        return exact, combined, under, distance, score

    return sorted(candidates, key=rank, reverse=True)


def _download_youtube_with_live_browser_fallback(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
) -> dict[str, Any]:
    effective = "best" if preferred == "audio" else preferred
    try:
        result = self._download_ytdlp(url, outdir, effective)
    except DownloadError as primary_error:
        # Never turn yt-dlp metadata URLs or HTML storyboards into a second
        # download attempt. Only URLs observed live by Chromium's CDP are bound
        # to the active browser session and are eligible here. `discover` may be
        # supplied by a prior/live capture even when new browser capture is off.
        discovery = self.discover(url, skip_ytdlp=True)
        candidates = _live_cdp_candidates(discovery, effective)
        if not candidates:
            raise DownloadError(
                f"youtube_browser_no_media: {str(primary_error)[:160]}"
            ) from primary_error

        last_error = str(primary_error)
        for index, candidate in enumerate(candidates[:8]):
            attempt_dir = Path(outdir) / f"cdp-{index}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            try:
                candidate_result = self._download_candidate(candidate, attempt_dir, referer=url)
                media_path = Path(candidate_result["path"])
                if not extractor_module._has_audio(media_path):
                    last_error = "cdp candidate has no audio"
                    continue
                if not extractor_module._has_video(media_path):
                    last_error = "cdp candidate has no video"
                    continue
                result = candidate_result
                break
            except Exception as candidate_error:  # noqa: BLE001
                last_error = str(candidate_error)[:200]
        else:
            raise DownloadError(
                f"youtube_browser_fallback_failed: {last_error}"
            ) from primary_error

    if preferred == "audio":
        return self._convert_to_audio_only(result, Path(outdir))
    return result


def _patched_download(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str = "best",
    title_hint: str = "",
) -> dict[str, Any]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if _is_youtube_url(url):
        return _download_youtube_with_live_browser_fallback(
            self,
            url,
            outdir,
            preferred,
        )
    if preferred != "audio":
        return _ORIGINAL_DOWNLOAD(self, url, outdir, preferred, title_hint)
    # Use the exact path already proven by the video button, then extract audio
    # from that complete media file. This avoids broken platform audio-only URLs.
    video_result = _ORIGINAL_DOWNLOAD(self, url, outdir, "best", title_hint)
    return self._convert_to_audio_only(video_result, outdir)


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
        "youtube_browser_no_media",
        "youtube_browser_fallback_failed",
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
