from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import cookie_file_for_url, ffmpeg_location, javascript_runtime_options


_PATCHED = False


def _common_options(extractor: Any, *, skip_download: bool = False) -> dict[str, Any]:
    from .extractor import _ytdlp_headers

    options: dict[str, Any] = {
        "quiet": True,
        "noplaylist": True,
        "no_warnings": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "http_headers": _ytdlp_headers(),
        "source_address": "0.0.0.0",
    }
    if skip_download:
        options["skip_download"] = True
    options.update(javascript_runtime_options())
    if shutil.which("node") or shutil.which("deno"):
        # The default yt-dlp extra installs yt-dlp-ejs. Keep this as a safe
        # fallback when its bundled version is temporarily unavailable.
        options["remote_components"] = {"ejs:github"}
    if extractor.cookies_file:
        options["cookiefile"] = extractor.cookies_file
    return options


def install_runtime_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return

    from .extractor import DownloadError, UniversalExtractor

    original_base = UniversalExtractor._base_ytdlp_opts
    original_download = UniversalExtractor._download_ytdlp

    def base_options(self: Any, outdir: Path | str, preferred: str = "best") -> dict[str, Any]:
        options = original_base(self, outdir, preferred)
        options["ffmpeg_location"] = ffmpeg_location()
        options.update(javascript_runtime_options())
        if shutil.which("node") or shutil.which("deno"):
            options["remote_components"] = {"ejs:github"}
        # 🎯 مهم: عميل android/web/ios — إصلاح مجرّب يتجاوز حجب يوتيوب
        # (لا تحذف extractor_args — حذفها يعيد LOGIN_REQUIRED على يوتيوب)
        options["source_address"] = "0.0.0.0"
        options["no_warnings"] = False
        return options

    def ytdlp_info(self: Any, url: str) -> dict[str, Any]:
        from yt_dlp import YoutubeDL

        if not self.cookies_file:
            self.cookies_file = cookie_file_for_url(url)
        options = _common_options(self, skip_download=True)
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise DownloadError("yt-dlp لم يُرجع بيانات")
        if info.get("is_live"):
            raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
        return info

    def download_ytdlp(self: Any, url: str, outdir: Path, preferred: str) -> dict[str, Any]:
        if not self.cookies_file:
            self.cookies_file = cookie_file_for_url(url)
        return original_download(self, url, outdir, preferred)

    UniversalExtractor._base_ytdlp_opts = base_options
    UniversalExtractor._ytdlp_info = ytdlp_info
    UniversalExtractor._download_ytdlp = download_ytdlp
    _PATCHED = True
