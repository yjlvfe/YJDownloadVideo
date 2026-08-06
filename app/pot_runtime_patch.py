from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import extractor as extractor_module
from .config import cookie_file_for_url, settings
from .extractor import DownloadError, UniversalExtractor, _ytdlp_headers
from .youtube_policy import (
    BOT_CHECK,
    LOGIN_REQUIRED,
    RATE_LIMITED,
    YouTubeCircuit,
    classify_youtube_error,
)

_PATCHED = False
_ORIGINAL_INIT = UniversalExtractor.__init__
_ORIGINAL_BASE_OPTIONS = UniversalExtractor._base_ytdlp_opts
_ORIGINAL_DOWNLOAD = UniversalExtractor.download
_ORIGINAL_INFO = UniversalExtractor._ytdlp_info
_CIRCUIT_STORE: dict[str, object] = {}
_CIRCUIT = YouTubeCircuit(
    store=_CIRCUIT_STORE,
    cooldown_seconds=settings.youtube_circuit_cooldown_seconds,
)


def get_youtube_circuit() -> YouTubeCircuit:
    return _CIRCUIT


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


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


def _provider_extractor_args() -> dict[str, dict[str, list[str]]]:
    provider_url = os.getenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
    visitor_data = os.getenv("YOUTUBE_VISITOR_DATA", "").strip()
    youtube: dict[str, list[str]] = {
        "player_client": ["mweb"],
        "fetch_pot": ["always"],
        "pot_trace": ["false"],
        "use_ad_playback_context": ["true"],
        "player_skip": ["webpage", "configs"],
    }
    if visitor_data:
        youtube["visitor_data"] = [visitor_data]
    return {
        "youtube": youtube,
        "youtubepot-bgutilhttp": {"base_url": [provider_url]},
    }


def _fallback_extractor_args() -> dict[str, dict[str, list[str]]]:
    return {
        "youtube": {
            "player_client": ["web_safari"],
            "fetch_pot": ["never"],
            "player_skip": ["webpage", "configs"],
        }
    }


def _patched_init(
    self: UniversalExtractor,
    cookies_file: str | None = None,
    use_browser: bool = True,
    progress_hook=None,
    max_filesize: int = 0,
    max_duration: int = 0,
    browser_bin: str = "chromium",
    request_timeout: int = 30,
    proxy_url: str = "",
    referer_headers: bool = True,
) -> None:
    _ORIGINAL_INIT(
        self,
        cookies_file=cookies_file,
        use_browser=use_browser,
        progress_hook=progress_hook,
        max_filesize=max_filesize,
        max_duration=max_duration,
        browser_bin=browser_bin,
        request_timeout=request_timeout,
        referer_headers=referer_headers,
    )
    self.proxy_url = str(proxy_url or "").strip()


def _youtube_format(preferred: str, mode: str) -> str | None:
    if mode != "fallback":
        return None
    value = str(preferred or "best").strip().lower()
    if value == "audio":
        return "bestaudio/best"
    if value.isdigit():
        height = int(value)
        return f"best[protocol^=m3u8][height<={height}]/best[height<={height}]/best"
    return "best[protocol^=m3u8]/best"


def _youtube_options(
    self: UniversalExtractor,
    outdir: Path | str,
    preferred: str,
    mode: str,
) -> dict[str, Any]:
    options = _ORIGINAL_BASE_OPTIONS(self, outdir, preferred)
    options["extractor_args"] = (
        _provider_extractor_args() if mode == "primary" else _fallback_extractor_args()
    )
    options["js_runtimes"] = {"node": {}}
    options["retries"] = 1
    options["fragment_retries"] = 1
    options["concurrent_fragment_downloads"] = 1
    options["socket_timeout"] = max(20, int(self.request_timeout))
    options["no_warnings"] = False
    options["http_headers"] = _ytdlp_headers()

    cookie = self.cookies_file or cookie_file_for_url(str(getattr(self, "_runtime_url", "") or ""))
    if cookie:
        self.cookies_file = cookie
        options["cookiefile"] = cookie
    proxy = str(getattr(self, "proxy_url", "") or "").strip()
    if proxy:
        options["proxy"] = proxy

    fallback_format = _youtube_format(preferred, mode)
    if fallback_format:
        options["format"] = fallback_format
    return options


def _patched_base_options(
    self: UniversalExtractor,
    outdir: Path | str,
    preferred: str = "best",
) -> dict[str, Any]:
    active_url = str(getattr(self, "_runtime_url", "") or "")
    if not _is_youtube_url(active_url):
        return _ORIGINAL_BASE_OPTIONS(self, outdir, preferred)
    return _youtube_options(self, outdir, preferred, "primary")


def _latest_media_file(directory: Path) -> Path | None:
    media_suffixes = {".mp4", ".m4a", ".webm", ".mkv", ".mov", ".mp3"}
    files = [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in media_suffixes
        and not path.name.endswith((".part", ".ytdl", ".temp"))
    ]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def _download_youtube_attempt(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
    mode: str,
) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    attempt_dir = Path(outdir) / f"youtube-{mode}"
    shutil.rmtree(attempt_dir, ignore_errors=True)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    options = _youtube_options(self, attempt_dir, preferred, mode)
    options["outtmpl"] = str(attempt_dir / "%(title).120s.%(ext)s")

    try:
        with YoutubeDL(options) as ydl:
            extracted = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(attempt_dir, ignore_errors=True)
        raise DownloadError(classify_youtube_error(exc)) from exc

    info = extracted if isinstance(extracted, dict) else {}
    path = _latest_media_file(attempt_dir)
    if path is None:
        raise DownloadError("youtube_no_media_file")
    if not extractor_module._has_audio(path):
        raise DownloadError("youtube_output_without_audio")
    if preferred != "audio" and not extractor_module._has_video(path):
        raise DownloadError("youtube_output_without_video")
    if self.max_duration and info.get("duration") and info["duration"] > self.max_duration:
        raise DownloadError("مدة الفيديو تتجاوز الحد المسموح")
    if self.max_filesize and path.stat().st_size > self.max_filesize:
        raise DownloadError("حجم الفيديو يتجاوز الحد المسموح")
    return {
        "path": str(path),
        "title": info.get("title") or "",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail") or "",
        "ext": path.suffix.lstrip("."),
    }


def _terminal_without_fallback(classification: str) -> bool:
    return classification in {RATE_LIMITED, BOT_CHECK, LOGIN_REQUIRED, "youtube_circuit_open"}


def _patched_download(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str = "best",
    title_hint: str = "",
) -> dict[str, Any]:
    if not _is_youtube_url(url):
        return _ORIGINAL_DOWNLOAD(self, url, outdir, preferred, title_hint)
    if not _CIRCUIT.allow_request():
        raise DownloadError("youtube_circuit_open")

    canonical = _canonical_youtube_url(url)
    previous_url = getattr(self, "_runtime_url", None)
    self._runtime_url = canonical
    if not self.cookies_file:
        self.cookies_file = cookie_file_for_url(canonical)
    try:
        try:
            result = _download_youtube_attempt(self, canonical, Path(outdir), preferred, "primary")
        except DownloadError as primary_error:
            classification = classify_youtube_error(primary_error)
            if _terminal_without_fallback(classification):
                _CIRCUIT.record_failure(classification)
                raise DownloadError(classification) from primary_error
            try:
                result = _download_youtube_attempt(
                    self,
                    canonical,
                    Path(outdir),
                    preferred,
                    "fallback",
                )
            except DownloadError as fallback_error:
                final_classification = classify_youtube_error(fallback_error)
                _CIRCUIT.record_failure(final_classification)
                raise DownloadError(final_classification) from fallback_error
        _CIRCUIT.record_success()
        return result
    finally:
        if previous_url is None:
            try:
                delattr(self, "_runtime_url")
            except AttributeError:
                pass
        else:
            self._runtime_url = previous_url


def _extract_youtube_info(self: UniversalExtractor, url: str, mode: str) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    options = _youtube_options(self, Path("/tmp"), "best", mode)
    options.update({"skip_download": True, "noplaylist": True})
    options.pop("outtmpl", None)
    options.pop("progress_hooks", None)
    options.pop("postprocessors", None)
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise DownloadError("youtube_no_metadata")
    if info.get("is_live"):
        raise DownloadError("البث المباشر غير مدعوم")
    return info


def _patched_ytdlp_info(self: UniversalExtractor, url: str) -> dict[str, Any]:
    if not _is_youtube_url(url):
        return _ORIGINAL_INFO(self, url)
    if not _CIRCUIT.allow_request():
        raise DownloadError("youtube_circuit_open")

    canonical = _canonical_youtube_url(url)
    previous_url = getattr(self, "_runtime_url", None)
    self._runtime_url = canonical
    if not self.cookies_file:
        self.cookies_file = cookie_file_for_url(canonical)
    try:
        try:
            info = _extract_youtube_info(self, canonical, "primary")
        except Exception as primary_error:  # noqa: BLE001
            classification = classify_youtube_error(primary_error)
            if _terminal_without_fallback(classification):
                _CIRCUIT.record_failure(classification)
                raise DownloadError(classification) from primary_error
            try:
                info = _extract_youtube_info(self, canonical, "fallback")
            except Exception as fallback_error:  # noqa: BLE001
                final_classification = classify_youtube_error(fallback_error)
                _CIRCUIT.record_failure(final_classification)
                raise DownloadError(final_classification) from fallback_error
        _CIRCUIT.record_success()
        return info
    finally:
        if previous_url is None:
            try:
                delattr(self, "_runtime_url")
            except AttributeError:
                pass
        else:
            self._runtime_url = previous_url


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    UniversalExtractor.__init__ = _patched_init
    UniversalExtractor._base_ytdlp_opts = _patched_base_options
    UniversalExtractor.download = _patched_download
    UniversalExtractor._ytdlp_info = _patched_ytdlp_info
    _PATCHED = True


install()
