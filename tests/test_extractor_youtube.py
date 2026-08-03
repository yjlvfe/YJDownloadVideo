from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import config
from app.extractor import DownloadError, MediaCandidate, UniversalExtractor
from app.media_choice import selector_for_quality
from app.platform_runtime import install_runtime_patches


install_runtime_patches()


def test_ytdlp_options_use_registry_and_bounded_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config.settings, "youtube_proxy_url", "http://proxy.example:8080")
    extractor = UniversalExtractor(use_browser=False)
    extractor._runtime_url = "https://youtube.com/watch?v=abcdefghijk"

    opts = extractor._base_ytdlp_opts(tmp_path, "720")

    assert opts["format"] == selector_for_quality("720")
    assert opts["retries"] == 1
    assert opts["fragment_retries"] == 1
    assert opts["proxy"] == "http://proxy.example:8080"
    clients = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", [])
    assert clients == ["android"]


def test_youtube_proxy_never_leaks_to_other_platforms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config.settings, "youtube_proxy_url", "http://proxy.example:8080")
    extractor = UniversalExtractor(use_browser=False)
    extractor._runtime_url = "https://www.instagram.com/reel/example/"

    opts = extractor._base_ytdlp_opts(tmp_path, "720")

    assert "proxy" not in opts


def test_public_youtube_fallback_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config.settings, "enable_public_youtube_fallback", False)
    extractor = UniversalExtractor(use_browser=False)

    with pytest.raises(DownloadError, match="disabled"):
        extractor._download_yt_fallback(
            "https://youtube.com/shorts/T7DA7VTZ_Zs",
            tmp_path,
            "best",
        )


def test_signed_googlevideo_403_is_not_replayed_through_generic_ytdlp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = httpx.Request("GET", "https://rr1---sn.example.googlevideo.com/videoplayback?expire=1")
    response = httpx.Response(403, request=request)

    class StreamResponse:
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=request, response=response)

    monkeypatch.setattr(httpx, "stream", lambda *args, **kwargs: StreamResponse())

    calls: list[str] = []

    class ForbiddenYoutubeDL:
        def __init__(self, *args, **kwargs):
            calls.append("constructed")

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=ForbiddenYoutubeDL))

    candidate = MediaCandidate(
        url=str(request.url),
        kind="direct",
        source="ytdlp",
        title="test",
    )
    extractor = UniversalExtractor(use_browser=False)

    with pytest.raises(DownloadError, match="youtube_fragment_forbidden"):
        extractor._download_candidate(candidate, tmp_path, referer="https://youtube.com/watch?v=abcdefghijk")

    assert calls == []
