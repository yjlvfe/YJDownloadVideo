from __future__ import annotations

from .config import settings
from .extractor import UniversalExtractor
from .pot_runtime_patch import get_youtube_circuit, install

_PROXY_DEFAULT_PATCHED = False


def _install_proxy_default() -> None:
    global _PROXY_DEFAULT_PATCHED
    if _PROXY_DEFAULT_PATCHED:
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

    UniversalExtractor.__init__ = init_with_configured_proxy
    _PROXY_DEFAULT_PATCHED = True


def install_runtime_patches() -> None:
    install()
    _install_proxy_default()


install_runtime_patches()

__all__ = ["get_youtube_circuit", "install_runtime_patches"]
