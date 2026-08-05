from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import cookie_file_for_url, settings
from .extractor import DownloadError, UniversalExtractor, _ytdlp_headers
from . import pot_runtime_patch as pot_patch


_PATCHED = False
_ORIGINAL_INIT = UniversalExtractor.__init__
_ORIGINAL_BASE_OPTIONS = UniversalExtractor._base_ytdlp_opts
_ORIGINAL_DOWNLOAD = UniversalExtractor.download
_ORIGINAL_INSPECT = UniversalExtractor.inspect
_ORIGINAL_YTDLP_INFO = UniversalExtractor._ytdlp_info


def _youtube_proxy(url: str) -> str:
    if not pot_patch._is_youtube_url(url):
        return ""
    return str(settings.youtube_proxy_url or "").strip()


def _effective_proxy(extractor: UniversalExtractor, url: str = "") -> str:
    configured = str(getattr(extractor, "proxy_url", "") or "").strip()
    return configured or _youtube_proxy(url)


def _ensure_credentials(extractor: UniversalExtractor, url: str) -> tuple[str | None, str]:
    if not extractor.cookies_file:
        extractor.cookies_file = cookie_file_for_url(url)
    proxy = _effective_proxy(extractor, url)
    if proxy:
        extractor.proxy_url = proxy
    return extractor.cookies_file, proxy


@contextmanager
def _proxy_environment(proxy: str) -> Iterator[None]:
    value = str(proxy or "").strip()
    if not value:
        yield
        return

    names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = value
        yield
    finally:
        for name, old_value in previous.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value


def _patched_init(
    self: UniversalExtractor,
    cookies_file: str | None = None,
    use_browser: bool = True,
    progress_hook=None,
    max_filesize: int = 0,
    max_duration: int = 0,
    browser_bin: str = "chromium",
    request_timeout: int = 30,
    referer_headers: bool = True,
    proxy_url: str = "",
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


def _patched_base_options(
    self: UniversalExtractor,
    outdir: Path | str,
    preferred: str = "best",
) -> dict[str, Any]:
    options = _ORIGINAL_BASE_OPTIONS(self, outdir, preferred)
    proxy = _effective_proxy(self)
    if proxy:
        options["proxy"] = proxy
    return options


def _patched_ytdlp_info(self: UniversalExtractor, url: str) -> dict[str, Any]:
    if not pot_patch._is_youtube_url(url):
        with _proxy_environment(_effective_proxy(self, url)):
            return _ORIGINAL_YTDLP_INFO(self, url)

    cookies_file, proxy = _ensure_credentials(self, url)
    from yt_dlp import YoutubeDL

    source_url = pot_patch._canonical_youtube_url(url)
    options: dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "no_warnings": True,
        "socket_timeout": 45,
        "http_headers": _ytdlp_headers(),
        "extractor_args": pot_patch._provider_extractor_args(),
        "js_runtimes": {"node": {}},
    }
    if pot_patch._is_shorts_url(url):
        options["extractor_args"] = {
            "youtube": {
                "player_client": ["web_safari", "web_embedded", "android_vr"],
                "fetch_pot": ["never"],
                "player_skip": ["webpage", "configs"],
            }
        }
    if cookies_file:
        options["cookiefile"] = cookies_file
    if proxy:
        options["proxy"] = proxy

    with _proxy_environment(proxy):
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(source_url, download=False)
    if not info:
        raise DownloadError("yt-dlp لم يُرجع بيانات")
    if info.get("is_live"):
        raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
    return info


def _patched_download(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str = "best",
    title_hint: str = "",
) -> dict[str, Any]:
    _, proxy = _ensure_credentials(self, url)
    with _proxy_environment(proxy):
        return _ORIGINAL_DOWNLOAD(self, url, outdir, preferred, title_hint)


def _patched_inspect(self: UniversalExtractor, url: str) -> dict[str, Any]:
    _, proxy = _ensure_credentials(self, url)
    with _proxy_environment(proxy):
        return _ORIGINAL_INSPECT(self, url)


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    UniversalExtractor.__init__ = _patched_init
    UniversalExtractor._base_ytdlp_opts = _patched_base_options
    UniversalExtractor._ytdlp_info = _patched_ytdlp_info
    UniversalExtractor.download = _patched_download
    UniversalExtractor.inspect = _patched_inspect
    _PATCHED = True


install()
