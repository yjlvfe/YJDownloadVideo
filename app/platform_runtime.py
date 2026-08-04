from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import (
    cookie_file_for_url,
    ffmpeg_location,
    javascript_runtime_options,
    settings,
)
from .media_choice import is_quality_key, selector_for_quality
from .redaction import SafeYtdlpLogger
from .youtube_policy import (
    FRAGMENT_FORBIDDEN,
    YouTubeCircuit,
    classify_youtube_error,
    youtube_retry_allowed,
)


_PATCHED = False
_YOUTUBE_CIRCUIT_STORE: dict[str, object] = {}
_YOUTUBE_CIRCUIT = YouTubeCircuit(
    store=_YOUTUBE_CIRCUIT_STORE,
    cooldown_seconds=settings.youtube_circuit_cooldown_seconds,
)


def get_youtube_circuit() -> YouTubeCircuit:
    return _YOUTUBE_CIRCUIT


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "youtu.be" or host.endswith("youtube.com")


def _is_googlevideo_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower()
    return host == "googlevideo.com" or host.endswith(".googlevideo.com")


def _trusted_googlevideo_candidate(candidate: Any) -> bool:
    source = str(getattr(candidate, "source", "") or "").lower()
    cookies_file = str(getattr(candidate, "cookies_file", "") or "").strip()
    return source in {"cdp", "cdp-dom"} and bool(cookies_file)


def _safe_ytdlp_logger() -> SafeYtdlpLogger:
    return SafeYtdlpLogger(
        secrets_to_hide=(
            settings.telegram_bot_token,
            settings.telegram_api_hash,
            settings.worker_shared_secret,
            settings.admin_api_token,
            settings.youtube_proxy_url,
        )
    )


def _common_options(
    extractor: Any,
    *,
    skip_download: bool = False,
    url: str = "",
) -> dict[str, Any]:
    from .extractor import _ytdlp_headers

    options: dict[str, Any] = {
        "quiet": True,
        "noplaylist": True,
        "no_warnings": False,
        "socket_timeout": 30,
        "retries": 1,
        "fragment_retries": 1,
        "concurrent_fragment_downloads": 1,
        "http_headers": _ytdlp_headers(),
        "source_address": "0.0.0.0",
        "logger": _safe_ytdlp_logger(),
    }
    if skip_download:
        options["skip_download"] = True
    options.update(javascript_runtime_options())
    if extractor.cookies_file:
        options["cookiefile"] = extractor.cookies_file
    if _is_youtube_url(url):
        if settings.youtube_proxy_url:
            options["proxy"] = settings.youtube_proxy_url
        # One stable client only. Trying several clients after a 429 multiplies
        # the requests from the same blocked Render egress and worsens the ban.
        options["extractor_args"] = {"youtube": {"player_client": ["android"]}}
    return options


def _latest_media_file(outdir: Path) -> Path | None:
    candidates = [
        path
        for path in outdir.iterdir()
        if path.is_file() and not path.name.endswith((".part", ".ytdl", ".temp"))
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def install_runtime_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from .extractor import (
        DownloadError,
        UniversalExtractor,
        _has_audio,
        _has_video,
    )

    original_base = UniversalExtractor._base_ytdlp_opts
    original_download = UniversalExtractor._download_ytdlp
    original_fallback = UniversalExtractor._download_yt_fallback
    original_candidate = UniversalExtractor._download_candidate

    def base_options(self: Any, outdir: Path | str, preferred: str = "best") -> dict[str, Any]:
        options = original_base(self, outdir, preferred)
        if is_quality_key(preferred, include_audio=True):
            options["format"] = selector_for_quality(preferred)
        options["ffmpeg_location"] = ffmpeg_location()
        options["retries"] = 1
        options["fragment_retries"] = 1
        options["concurrent_fragment_downloads"] = 1
        options["source_address"] = "0.0.0.0"
        options["no_warnings"] = False
        options["logger"] = _safe_ytdlp_logger()
        options.update(javascript_runtime_options())

        active_url = str(getattr(self, "_runtime_url", "") or "")
        if _is_youtube_url(active_url):
            options["extractor_args"] = {"youtube": {"player_client": ["android"]}}
            if settings.youtube_proxy_url:
                options["proxy"] = settings.youtube_proxy_url
            else:
                options.pop("proxy", None)
        else:
            # YouTube's proxy/cookies/client identity must never alter TikTok,
            # Instagram, or generic site downloads.
            options.pop("proxy", None)
            options.pop("extractor_args", None)
        return options

    def ytdlp_info(self: Any, url: str) -> dict[str, Any]:
        from yt_dlp import YoutubeDL

        is_youtube = _is_youtube_url(url)
        if is_youtube and not _YOUTUBE_CIRCUIT.allow_request():
            raise DownloadError("youtube_circuit_open")
        if not self.cookies_file:
            self.cookies_file = cookie_file_for_url(url)
        options = _common_options(self, skip_download=True, url=url)
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            if is_youtube:
                classification = classify_youtube_error(exc)
                _YOUTUBE_CIRCUIT.record_failure(classification)
                raise DownloadError(classification) from exc
            raise
        if not info:
            raise DownloadError("yt-dlp لم يُرجع بيانات")
        if info.get("is_live"):
            raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
        if is_youtube:
            _YOUTUBE_CIRCUIT.record_success()
        return info

    def download_ytdlp(self: Any, url: str, outdir: Path, preferred: str) -> dict[str, Any]:
        from yt_dlp import YoutubeDL

        previous_url = getattr(self, "_runtime_url", None)
        self._runtime_url = url
        try:
            if not self.cookies_file:
                self.cookies_file = cookie_file_for_url(url)
            if not _is_youtube_url(url):
                return original_download(self, url, outdir, preferred)
            if not _YOUTUBE_CIRCUIT.allow_request():
                raise DownloadError("youtube_circuit_open")

            outdir = Path(outdir)
            outdir.mkdir(parents=True, exist_ok=True)
            attempt = 0
            info: dict[str, Any] | None = None
            while True:
                options = self._base_ytdlp_opts(outdir, preferred)
                if attempt:
                    options["outtmpl"] = str(outdir / "%(title).120s_reextract.%(ext)s")
                try:
                    with YoutubeDL(options) as ydl:
                        extracted = ydl.extract_info(url, download=True)
                    info = extracted if isinstance(extracted, dict) else {}
                    break
                except Exception as exc:  # noqa: BLE001
                    classification = classify_youtube_error(exc)
                    if youtube_retry_allowed(classification, attempt):
                        attempt += 1
                        continue
                    _YOUTUBE_CIRCUIT.record_failure(classification)
                    raise DownloadError(classification) from exc

            path = _latest_media_file(outdir)
            if path is None:
                raise DownloadError("لم يُنشأ ملف")
            if not _has_audio(path):
                raise DownloadError("المقطع المتاح بدون صوت")
            if preferred != "audio" and not _has_video(path):
                raise DownloadError("المقطع المتاح بدون فيديو")
            if self.max_duration and info.get("duration") and info["duration"] > self.max_duration:
                raise DownloadError("مدة الفيديو تتجاوز الحد المسموح")
            if self.max_filesize and path.stat().st_size > self.max_filesize:
                raise DownloadError("حجم الفيديو يتجاوز الحد المسموح")

            _YOUTUBE_CIRCUIT.record_success()
            return {
                "path": str(path),
                "title": info.get("title") or "",
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail") or "",
                "ext": path.suffix.lstrip("."),
            }
        finally:
            if previous_url is None:
                try:
                    delattr(self, "_runtime_url")
                except AttributeError:
                    pass
            else:
                self._runtime_url = previous_url

    def youtube_fallback(self: Any, url: str, outdir: Path, preferred: str) -> dict[str, Any]:
        if not settings.enable_public_youtube_fallback:
            raise DownloadError("public youtube fallback disabled")
        return original_fallback(self, url, outdir, preferred)

    def download_candidate(self: Any, candidate: Any, outdir: Path, referer: str) -> dict[str, Any]:
        # googlevideo URLs are short-lived and bound to the extraction identity.
        # Replaying one through a generic downloader or a different client is
        # exactly what produced the repeated 403s observed on Render.
        if (
            _is_googlevideo_url(str(candidate.url))
            and not _trusted_googlevideo_candidate(candidate)
        ):
            raise DownloadError(FRAGMENT_FORBIDDEN)
        return original_candidate(self, candidate, outdir, referer)

    UniversalExtractor._base_ytdlp_opts = base_options
    UniversalExtractor._ytdlp_info = ytdlp_info
    UniversalExtractor._download_ytdlp = download_ytdlp
    UniversalExtractor._download_yt_fallback = youtube_fallback
    UniversalExtractor._download_candidate = download_candidate
    _PATCHED = True
