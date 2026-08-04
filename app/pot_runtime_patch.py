from __future__ import annotations

import os
import re
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


def _browser_path() -> str:
    return os.getenv("WPC_BROWSER_BIN", "/usr/local/bin/chromium-wpc").strip() or "/usr/local/bin/chromium-wpc"


def _provider_extractor_args() -> dict[str, dict[str, list[str]]]:
    provider_url = os.getenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "").strip()
    youtube_args: dict[str, list[str]] = {
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
        "youtubepot-wpc": {
            "browser_path": [_browser_path()],
        },
    }


def _wpc_only_extractor_args() -> dict[str, dict[str, list[str]]]:
    return {
        "youtube": {
            "player_client": ["mweb"],
            "fetch_pot": ["always"],
            "pot_trace": ["false"],
            "use_ad_playback_context": ["true"],
        },
        "youtubepot-bgutilhttp": {
            "base_url": ["http://127.0.0.1:1"],
        },
        "youtubepot-wpc": {
            "browser_path": [_browser_path()],
        },
    }


def _patched_base_options(
    self: UniversalExtractor,
    outdir: Path | str,
    preferred: str = "best",
) -> dict[str, Any]:
    effective = "best" if preferred == "audio" else preferred
    options = _ORIGINAL_BASE_OPTIONS(self, outdir, effective)
    options["extractor_args"] = _provider_extractor_args()
    options["js_runtimes"] = {"node": {}}
    return options


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def _is_shorts_url(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com/|youtu\.be/)?shorts/[A-Za-z0-9_-]{11}", str(url or "")))


def _canonical_youtube_url(url: str) -> str:
    raw = str(url or "").strip()
    if not _is_youtube_url(raw):
        return raw
    match = re.search(
        r"(?:[?&]v=|shorts/|youtu\.be/|embed/|live/)([A-Za-z0-9_-]{11})",
        raw,
    )
    if not match:
        return raw
    return f"https://www.youtube.com/watch?v={match.group(1)}"


def _candidate_itag(url: str) -> int:
    try:
        value = (parse_qs(urlparse(str(url)).query).get("itag") or ["0"])[0]
        return int(value)
    except (TypeError, ValueError):
        return 0


def _live_cdp_candidates(discovery: dict[str, Any], preferred: str) -> list[Any]:
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


def _media_result(
    info: dict[str, Any],
    directory: Path,
    preferred: str,
    *,
    error_code: str,
) -> dict[str, Any]:
    media_extensions = {".mp4", ".m4a", ".webm", ".mkv", ".mov", ".mp3"}
    files = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in media_extensions
    ]
    if not files:
        raise DownloadError(f"{error_code}: no media file was created")
    path = max(files, key=lambda item: item.stat().st_mtime)
    if not extractor_module._has_audio(path):
        raise DownloadError(f"{error_code}: output has no audio")
    if preferred != "audio" and not extractor_module._has_video(path):
        raise DownloadError(f"{error_code}: output has no video")
    return {
        "path": str(path),
        "title": info.get("title") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail") or "",
        "ext": path.suffix.lstrip("."),
    }


def _height_selector(preferred: str, *, hls: bool) -> str:
    protocol = "[protocol^=m3u8]" if hls else ""
    if str(preferred).isdigit():
        height = int(preferred)
        return f"best{protocol}[height<={height}]/best[height<={height}]/best"
    return f"best{protocol}/best" if hls else "bestvideo*+bestaudio/best"


def _download_youtube_lightweight(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
) -> dict[str, Any]:
    """Download Shorts without launching WPC's headed Chromium.

    These clients currently do not require a GVS token for the selected paths:
    web_safari HLS first, then embeddable/VR clients. Every attempt is bounded
    and validated with ffprobe before it can be returned.
    """
    from yt_dlp import YoutubeDL

    effective = "best" if preferred == "audio" else preferred
    attempts = (
        ("web_safari", True),
        ("web_embedded", False),
        ("android_vr", False),
    )
    errors: list[str] = []
    for client, hls in attempts:
        attempt_dir = Path(outdir) / f"light-{client}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        options = _ORIGINAL_BASE_OPTIONS(self, attempt_dir, effective)
        options["extractor_args"] = {
            "youtube": {
                "player_client": [client],
                "fetch_pot": ["never"],
                "player_skip": ["webpage", "configs"],
            }
        }
        options["js_runtimes"] = {"node": {}}
        options["format"] = _height_selector(effective, hls=hls)
        options["outtmpl"] = str(attempt_dir / "%(title).120s.%(ext)s")
        options["retries"] = 1
        options["fragment_retries"] = 1
        options["socket_timeout"] = 25
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            if not isinstance(info, dict):
                raise DownloadError("no metadata returned")
            return _media_result(
                info,
                attempt_dir,
                effective,
                error_code="youtube_lightweight_failed",
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{client}: {str(exc)[:120]}")
    raise DownloadError("youtube_lightweight_failed: " + " / ".join(errors))


def _download_youtube_with_wpc(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    effective = "best" if preferred == "audio" else preferred
    attempt_dir = Path(outdir) / "wpc"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    options = _ORIGINAL_BASE_OPTIONS(self, attempt_dir, effective)
    options["extractor_args"] = _wpc_only_extractor_args()
    options["js_runtimes"] = {"node": {}}
    options["outtmpl"] = str(attempt_dir / "%(title).120s.%(ext)s")
    options["retries"] = 1
    options["fragment_retries"] = 1
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
        if not isinstance(info, dict):
            raise DownloadError("youtube_wpc_failed: no metadata returned")
        return _media_result(
            info,
            attempt_dir,
            effective,
            error_code="youtube_wpc_failed",
        )
    except DownloadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(f"youtube_wpc_failed: {str(exc)[:240]}") from exc


def _download_cdp_fallback(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
    previous_error: BaseException,
) -> dict[str, Any]:
    try:
        discovery = self.discover(url, skip_ytdlp=True)
    except DownloadError as browser_error:
        raise DownloadError(
            f"youtube_browser_no_media: {str(previous_error)[:160]} / {str(browser_error)[:120]}"
        ) from previous_error
    candidates = _live_cdp_candidates(discovery, preferred)
    if not candidates:
        raise DownloadError(
            f"youtube_browser_no_media: {str(previous_error)[:200]}"
        ) from previous_error

    last_error = str(previous_error)
    for index, candidate in enumerate(candidates[:8]):
        attempt_dir = Path(outdir) / f"cdp-{index}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = self._download_candidate(candidate, attempt_dir, referer=url)
            media_path = Path(result["path"])
            if not extractor_module._has_audio(media_path):
                last_error = "cdp candidate has no audio"
                continue
            if not extractor_module._has_video(media_path):
                last_error = "cdp candidate has no video"
                continue
            return result
        except Exception as candidate_error:  # noqa: BLE001
            last_error = str(candidate_error)[:200]
    raise DownloadError(
        f"youtube_browser_fallback_failed: {last_error}"
    ) from previous_error


def _download_youtube_with_live_browser_fallback(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
    *,
    shorts_mode: bool = False,
) -> dict[str, Any]:
    effective = "best" if preferred == "audio" else preferred
    if shorts_mode:
        try:
            result = _download_youtube_lightweight(self, url, outdir, effective)
        except DownloadError as lightweight_error:
            result = _download_cdp_fallback(
                self,
                url,
                outdir,
                effective,
                lightweight_error,
            )
    else:
        try:
            result = self._download_ytdlp(url, outdir, effective)
        except DownloadError as primary_error:
            try:
                result = _download_youtube_with_wpc(self, url, outdir, effective)
            except DownloadError as wpc_error:
                result = _download_cdp_fallback(
                    self,
                    url,
                    outdir,
                    effective,
                    wpc_error,
                )

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
        shorts_mode = _is_shorts_url(url)
        canonical_url = _canonical_youtube_url(url)
        return _download_youtube_with_live_browser_fallback(
            self,
            canonical_url,
            outdir,
            preferred,
            shorts_mode=shorts_mode,
        )
    if preferred != "audio":
        return _ORIGINAL_DOWNLOAD(self, url, outdir, preferred, title_hint)
    video_result = _ORIGINAL_DOWNLOAD(self, url, outdir, "best", title_hint)
    return self._convert_to_audio_only(video_result, outdir)


def _patched_ytdlp_info(self: UniversalExtractor, url: str) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    source_url = _canonical_youtube_url(url) if _is_youtube_url(url) else url
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
    if _is_shorts_url(url):
        options["extractor_args"] = {
            "youtube": {
                "player_client": ["web_safari", "web_embedded", "android_vr"],
                "fetch_pot": ["never"],
                "player_skip": ["webpage", "configs"],
            }
        }
    if self.cookies_file:
        options["cookiefile"] = self.cookies_file
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(source_url, download=False)
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
        "youtube_lightweight_failed",
        "youtube_wpc_failed",
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
