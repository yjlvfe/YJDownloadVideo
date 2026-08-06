from __future__ import annotations

from pathlib import Path

from .config import settings
from .extractor import DownloadError, UniversalExtractor
from .pot_runtime_patch import get_youtube_circuit, install
from .quality_runtime_patch import install as install_quality_runtime
from .telegram_quality_runtime_patch import install as install_telegram_quality_runtime
from .telegram_storage_guard import install as install_telegram_storage_guard

_COMPATIBILITY_GUARDS_INSTALLED = False


def _install_compatibility_guards() -> None:
    global _COMPATIBILITY_GUARDS_INSTALLED
    if _COMPATIBILITY_GUARDS_INSTALLED:
        return
    original_init = UniversalExtractor.__init__

    def init_with_configured_proxy(
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
        original_init(
            self,
            cookies_file=cookies_file,
            use_browser=use_browser,
            progress_hook=progress_hook,
            max_filesize=max_filesize,
            max_duration=max_duration,
            browser_bin=browser_bin,
            request_timeout=request_timeout,
            proxy_url=proxy_url or settings.youtube_proxy_url,
            referer_headers=referer_headers,
        )

    def disabled_public_youtube_fallback(
        self: UniversalExtractor,
        url: str,
        outdir: Path,
        preferred: str,
    ) -> dict:
        raise DownloadError("public youtube fallback disabled")

    UniversalExtractor.__init__ = init_with_configured_proxy
    UniversalExtractor._download_yt_fallback = disabled_public_youtube_fallback
    _COMPATIBILITY_GUARDS_INSTALLED = True


def install_runtime_patches() -> None:
    install()
    install_quality_runtime()
    install_telegram_quality_runtime()
    install_telegram_storage_guard()
    _install_compatibility_guards()


install_runtime_patches()

__all__ = ["get_youtube_circuit", "install_runtime_patches"]
