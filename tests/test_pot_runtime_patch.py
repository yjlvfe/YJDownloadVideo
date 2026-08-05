from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from app import extractor as extractor_module
from app import pot_runtime_patch as pot_patch_module
from app import youtube_strategy_runtime as strategy_module
from app.extractor import DownloadError, MediaCandidate, UniversalExtractor
from app.pot_runtime_patch import _canonical_youtube_url


def test_po_token_provider_uses_mweb_with_visitor_binding(monkeypatch):
    monkeypatch.setenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:5516/")
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "visitor-session-value")
    args = strategy_module._provider_extractor_args()
    youtube = args["youtube"]
    assert youtube["player_client"] == ["mweb"]
    assert youtube["fetch_pot"] == ["always"]
    assert youtube["visitor_data"] == ["visitor-session-value"]
    assert youtube["player_skip"] == ["webpage", "configs"]
    assert args["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:5516"]


def test_po_token_provider_omits_empty_visitor_binding(monkeypatch):
    monkeypatch.delenv("YOUTUBE_VISITOR_DATA", raising=False)
    args = strategy_module._provider_extractor_args()
    assert "visitor_data" not in args["youtube"]


def test_android_vr_uses_trusted_visitor_data_without_pot(monkeypatch):
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "trusted-visitor")
    youtube = strategy_module._client_extractor_args("android_vr")["youtube"]
    assert youtube["player_client"] == ["android_vr"]
    assert youtube["fetch_pot"] == ["never"]
    assert youtube["visitor_data"] == ["trusted-visitor"]
    assert youtube["player_skip"] == ["webpage", "configs"]


def test_base_options_include_provider_and_node_runtime(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "shared-binding")
    extractor = UniversalExtractor(use_browser=False)
    options = extractor._base_ytdlp_opts(tmp_path, "720")
    youtube = options["extractor_args"]["youtube"]
    assert youtube["player_client"] == ["mweb"]
    assert youtube["fetch_pot"] == ["always"]
    assert youtube["visitor_data"] == ["shared-binding"]
    assert youtube["player_skip"] == ["webpage", "configs"]
    assert options["js_runtimes"] == {"node": {}}
    assert "height<=720" in options["format"]


def test_browser_po_token_provider_is_installed_and_uses_wrapper(monkeypatch):
    monkeypatch.setenv("WPC_BROWSER_BIN", "/usr/local/bin/test-chromium-wpc")
    args = strategy_module._provider_extractor_args()
    assert importlib.metadata.version("yt-dlp-getpot-wpc") == "1.0.0"
    assert args["youtubepot-wpc"]["browser_path"] == ["/usr/local/bin/test-chromium-wpc"]


def test_browser_po_token_provider_defaults_to_safe_wrapper(monkeypatch):
    monkeypatch.delenv("WPC_BROWSER_BIN", raising=False)
    args = strategy_module._provider_extractor_args()
    assert args["youtubepot-wpc"]["browser_path"] == ["/usr/local/bin/chromium-wpc"]


def test_shorts_url_is_canonicalized_to_watch_url():
    assert _canonical_youtube_url(
        "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu"
    ) == "https://www.youtube.com/watch?v=T7DA7VTZ_Zs"
    assert _canonical_youtube_url(
        "https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce"
    ) == "https://www.youtube.com/watch?v=kTRs4ycFp_o"


def test_shorts_use_lightweight_clients_before_provider(monkeypatch, tmp_path: Path):
    extractor = UniversalExtractor(use_browser=True)
    output = tmp_path / "short.mp4"
    output.write_bytes(b"media")

    monkeypatch.setattr(
        strategy_module,
        "_download_youtube_lightweight",
        lambda *args, **kwargs: {
            "path": str(output),
            "title": "short",
            "duration": 10,
            "thumbnail": "",
            "ext": "mp4",
        },
    )
    monkeypatch.setattr(
        extractor,
        "_download_ytdlp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider should not run")),
    )

    result = extractor.download(
        "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu",
        tmp_path,
        "best",
    )
    assert result["path"] == str(output)


def test_standard_youtube_uses_wpc_only_with_credentials(monkeypatch, tmp_path: Path):
    extractor = UniversalExtractor(use_browser=True, cookies_file="/tmp/youtube-cookies.txt")
    output = tmp_path / "wpc.mp4"
    output.write_bytes(b"media")

    monkeypatch.setattr(
        extractor,
        "_download_ytdlp",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("youtube_fragment_forbidden")),
    )
    monkeypatch.setattr(
        strategy_module,
        "_download_youtube_lightweight",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("youtube_lightweight_failed")),
    )
    monkeypatch.setattr(
        pot_patch_module,
        "_download_youtube_with_wpc",
        lambda *args, **kwargs: {
            "path": str(output),
            "title": "wpc",
            "duration": 10,
            "thumbnail": "",
            "ext": "mp4",
        },
    )
    monkeypatch.setattr(
        pot_patch_module,
        "_download_cdp_fallback",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CDP should not run")),
    )

    result = extractor.download("https://youtu.be/kTRs4ycFp_o", tmp_path, "best")
    assert result["path"] == str(output)


def test_youtube_without_credentials_fails_before_browser_fallback(monkeypatch, tmp_path: Path):
    extractor = UniversalExtractor(use_browser=True)
    monkeypatch.delenv("YOUTUBE_ALLOW_BROWSER_FALLBACK", raising=False)
    monkeypatch.setattr(
        extractor,
        "_download_ytdlp",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("provider failed")),
    )
    monkeypatch.setattr(
        strategy_module,
        "_download_youtube_lightweight",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("lightweight failed")),
    )
    monkeypatch.setattr(
        pot_patch_module,
        "_download_youtube_with_wpc",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("WPC must not run")),
    )

    with pytest.raises(DownloadError, match="youtube_access_blocked"):
        extractor.download("https://youtu.be/kTRs4ycFp_o", tmp_path, "best")


def test_youtube_403_uses_only_live_cdp_candidates_when_credentials_allow_browser(
    monkeypatch,
    tmp_path: Path,
):
    extractor = UniversalExtractor(use_browser=True, cookies_file="/tmp/youtube-cookies.txt")
    youtube_url = "https://youtu.be/kTRs4ycFp_o"

    def blocked(*args, **kwargs):
        raise DownloadError("youtube_fragment_forbidden")

    monkeypatch.setattr(extractor, "_download_ytdlp", blocked)
    monkeypatch.setattr(strategy_module, "_download_youtube_lightweight", blocked)
    monkeypatch.setattr(pot_patch_module, "_download_youtube_with_wpc", blocked)

    storyboard = MediaCandidate(
        "https://i.ytimg.com/sb/kTRs4ycFp_o/storyboard3_L3/M$M.jpg",
        "direct",
        "js-blob",
    )
    live_cdp = MediaCandidate(
        "https://rr1---sn.example.googlevideo.com/videoplayback?itag=22",
        "direct",
        "cdp",
        height=720,
    )
    discover_calls: list[bool] = []

    def discover(url: str, skip_ytdlp: bool = False):
        discover_calls.append(skip_ytdlp)
        return {
            "source": "browser+html",
            "title": "sample",
            "thumbnail": "",
            "duration": 10,
            "candidates": [storyboard, live_cdp],
        }

    monkeypatch.setattr(extractor, "discover", discover)
    output = tmp_path / "sample.mp4"
    output.write_bytes(b"media")
    used_sources: list[str] = []

    def download_candidate(candidate, outdir, referer):
        used_sources.append(candidate.source)
        return {
            "path": str(output),
            "title": "sample",
            "duration": 10,
            "thumbnail": "",
            "ext": "mp4",
        }

    monkeypatch.setattr(extractor, "_download_candidate", download_candidate)
    monkeypatch.setattr(extractor_module, "_has_audio", lambda path: True)
    monkeypatch.setattr(extractor_module, "_has_video", lambda path: True)

    result = extractor.download(youtube_url, tmp_path, "720")

    assert result["path"] == str(output)
    assert discover_calls == [True]
    assert used_sources == ["cdp"]
