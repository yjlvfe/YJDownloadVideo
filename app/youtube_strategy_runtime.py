from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import durable_runtime
from . import network_runtime_patch as network_patch
from . import pot_runtime_patch as pot_patch
from .extractor import DownloadError, UniversalExtractor, _ytdlp_headers


_PATCHED = False


def _visitor_data() -> str:
    return os.getenv("YOUTUBE_VISITOR_DATA", "").strip()


def _client_extractor_args(client: str, *, fetch_pot: str = "never") -> dict[str, dict[str, list[str]]]:
    youtube: dict[str, list[str]] = {
        "player_client": [client],
        "fetch_pot": [fetch_pot],
        "player_skip": ["webpage", "configs"],
    }
    visitor_data = _visitor_data()
    if visitor_data:
        youtube["visitor_data"] = [visitor_data]
    return {"youtube": youtube}


def _provider_extractor_args() -> dict[str, dict[str, list[str]]]:
    provider_url = os.getenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
    youtube: dict[str, list[str]] = {
        "player_client": ["mweb"],
        "fetch_pot": ["always"],
        "pot_trace": ["false"],
        "use_ad_playback_context": ["true"],
        "player_skip": ["webpage", "configs"],
    }
    visitor_data = _visitor_data()
    if visitor_data:
        youtube["visitor_data"] = [visitor_data]
    return {
        "youtube": youtube,
        "youtubepot-bgutilhttp": {"base_url": [provider_url]},
        "youtubepot-wpc": {"browser_path": [pot_patch._browser_path()]},
    }


def _download_one_client(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
    *,
    client: str,
    hls: bool,
) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    effective = "best" if preferred == "audio" else preferred
    attempt_dir = Path(outdir) / f"light-{client}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    options = pot_patch._ORIGINAL_BASE_OPTIONS(self, attempt_dir, effective)
    options["extractor_args"] = _client_extractor_args(client)
    options["js_runtimes"] = {"node": {}}
    options["format"] = pot_patch._height_selector(effective, hls=hls)
    options["outtmpl"] = str(attempt_dir / "%(title).120s.%(ext)s")
    options["retries"] = 1
    options["fragment_retries"] = 1
    options["socket_timeout"] = 20
    options["sleep_interval_requests"] = 1
    proxy = str(getattr(self, "proxy_url", "") or "").strip()
    if proxy:
        options["proxy"] = proxy

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    if not isinstance(info, dict):
        raise DownloadError("no metadata returned")
    return pot_patch._media_result(
        info,
        attempt_dir,
        effective,
        error_code="youtube_lightweight_failed",
    )


def _download_youtube_lightweight(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
) -> dict[str, Any]:
    # android_vr currently does not require a GVS PO token. Embedded is useful
    # only for embeddable videos; Safari HLS is the final token-free attempt.
    attempts = (
        ("android_vr", False),
        ("web_embedded", False),
        ("web_safari", True),
    )
    errors: list[str] = []
    for client, hls in attempts:
        try:
            return _download_one_client(
                self,
                url,
                outdir,
                preferred,
                client=client,
                hls=hls,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{client}: {str(exc)[:140]}")
    raise DownloadError("youtube_lightweight_failed: " + " / ".join(errors))


def _browser_fallback_allowed(self: UniversalExtractor) -> bool:
    explicit = os.getenv("YOUTUBE_ALLOW_BROWSER_FALLBACK", "").strip().lower()
    return bool(
        self.use_browser
        and (
            self.cookies_file
            or str(getattr(self, "proxy_url", "") or "").strip()
            or explicit in {"1", "true", "yes", "on"}
        )
    )


def _download_youtube_strategy(
    self: UniversalExtractor,
    url: str,
    outdir: Path,
    preferred: str,
    *,
    shorts_mode: bool = False,
) -> dict[str, Any]:
    effective = "best" if preferred == "audio" else preferred
    errors: list[str] = []

    def provider_attempt() -> dict[str, Any]:
        return self._download_ytdlp(url, outdir, effective)

    def lightweight_attempt() -> dict[str, Any]:
        return _download_youtube_lightweight(self, url, outdir, effective)

    attempts = (
        ("lightweight", lightweight_attempt),
        ("mweb-pot", provider_attempt),
    ) if shorts_mode else (
        ("mweb-pot", provider_attempt),
        ("lightweight", lightweight_attempt),
    )

    result: dict[str, Any] | None = None
    for name, attempt in attempts:
        try:
            result = attempt()
            break
        except DownloadError as exc:
            errors.append(f"{name}: {str(exc)[:180]}")

    if result is None and _browser_fallback_allowed(self):
        try:
            result = pot_patch._download_youtube_with_wpc(self, url, outdir, effective)
        except DownloadError as wpc_error:
            errors.append(f"wpc: {str(wpc_error)[:180]}")
            result = pot_patch._download_cdp_fallback(
                self,
                url,
                outdir,
                effective,
                wpc_error,
            )

    if result is None:
        detail = " / ".join(errors)[-700:]
        raise DownloadError(
            "youtube_access_blocked: YouTube rejected the server session; "
            f"configure YouTube cookies or a proxy. {detail}"
        )

    if preferred == "audio":
        return self._convert_to_audio_only(result, Path(outdir))
    return result


def _extract_info_attempt(
    self: UniversalExtractor,
    url: str,
    extractor_args: dict[str, dict[str, list[str]]],
) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    options: dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "no_warnings": True,
        "retries": 1,
        "socket_timeout": 20,
        "sleep_interval_requests": 1,
        "http_headers": _ytdlp_headers(),
        "extractor_args": extractor_args,
        "js_runtimes": {"node": {}},
    }
    if self.cookies_file:
        options["cookiefile"] = self.cookies_file
    proxy = str(getattr(self, "proxy_url", "") or "").strip()
    if proxy:
        options["proxy"] = proxy
    with network_patch._proxy_environment(proxy):
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise DownloadError("yt-dlp لم يُرجع بيانات")
    if info.get("is_live"):
        raise DownloadError("البث المباشر غير مدعوم في هذا المسار")
    return info


def _strategy_ytdlp_info(self: UniversalExtractor, url: str) -> dict[str, Any]:
    if not pot_patch._is_youtube_url(url):
        return pot_patch._patched_ytdlp_info(self, url)

    source_url = pot_patch._canonical_youtube_url(url)
    errors: list[str] = []
    attempts = (
        ("android_vr", _client_extractor_args("android_vr")),
        ("mweb-pot", _provider_extractor_args()),
    )
    for name, extractor_args in attempts:
        try:
            return _extract_info_attempt(self, source_url, extractor_args)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {str(exc)[:180]}")
    raise DownloadError("youtube_access_blocked: " + " / ".join(errors))


def install() -> None:
    global _PATCHED
    if _PATCHED:
        return
    pot_patch._provider_extractor_args = _provider_extractor_args
    pot_patch._download_youtube_lightweight = _download_youtube_lightweight
    pot_patch._download_youtube_with_live_browser_fallback = _download_youtube_strategy
    UniversalExtractor._ytdlp_info = _strategy_ytdlp_info
    _PATCHED = True


install()
