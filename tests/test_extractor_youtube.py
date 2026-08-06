from __future__ import annotations

from pathlib import Path

import pytest

from app import config
from app.extractor import DownloadError, UniversalExtractor
from app.platform_runtime import install_runtime_patches


install_runtime_patches()


def test_ytdlp_options_use_registry_and_bounded_retries(tmp_path: Path) -> None:
    extractor = UniversalExtractor(
        use_browser=False,
        proxy_url="http://proxy.example:8080",
    )
    extractor._runtime_url = "https://youtube.com/watch?v=abcdefghijk"

    opts = extractor._base_ytdlp_opts(tmp_path, "720")

    assert "height<=720" in opts["format"]
    assert opts["retries"] == 1
    assert opts["fragment_retries"] == 1
    assert opts["proxy"] == "http://proxy.example:8080"
    youtube_args = opts.get("extractor_args", {}).get("youtube", {})
    assert youtube_args.get("player_client") == ["mweb"]
    assert youtube_args.get("fetch_pot") == ["always"]
    assert opts.get("extractor_args", {}).get("youtubepot-bgutilhttp", {}).get("base_url") == [
        "http://127.0.0.1:4416"
    ]


def test_youtube_proxy_never_leaks_to_other_platforms(tmp_path: Path) -> None:
    extractor = UniversalExtractor(
        use_browser=False,
        proxy_url="http://proxy.example:8080",
    )
    extractor._runtime_url = "https://www.instagram.com/reel/example/"

    opts = extractor._base_ytdlp_opts(tmp_path, "720")

    assert "proxy" not in opts


def test_public_youtube_fallback_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(config.settings, "enable_public_youtube_fallback", False)
    extractor = UniversalExtractor(use_browser=False)

    with pytest.raises(DownloadError, match="disabled"):
        extractor._download_yt_fallback(
            "https://youtube.com/shorts/T7DA7VTZ_Zs",
            tmp_path,
            "best",
        )
