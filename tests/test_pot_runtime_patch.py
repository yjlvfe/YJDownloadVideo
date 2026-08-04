from __future__ import annotations

import importlib.metadata
from pathlib import Path

from app import extractor as extractor_module
from app import pot_runtime_patch as pot_patch_module
from app.extractor import DownloadError, MediaCandidate, UniversalExtractor
from app.pot_runtime_patch import _canonical_youtube_url, _provider_extractor_args


def test_po_token_provider_uses_default_clients_then_mweb_with_visitor_binding(monkeypatch):
    monkeypatch.setenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:5516/")
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "visitor-session-value")
    args = _provider_extractor_args()
    youtube = args["youtube"]
    assert youtube["player_client"] == ["default", "mweb"]
    assert youtube["fetch_pot"] == ["always"]
    assert youtube["visitor_data"] == ["visitor-session-value"]
    assert youtube["player_skip"] == ["webpage", "configs"]
    assert args["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:5516"]


def test_po_token_provider_omits_empty_visitor_binding(monkeypatch):
    monkeypatch.delenv("YOUTUBE_VISITOR_DATA", raising=False)
    args = _provider_extractor_args()
    assert "visitor_data" not in args["youtube"]


def test_base_options_include_provider_and_node_runtime(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "shared-binding")
    extractor = UniversalExtractor(use_browser=False)
    options = extractor._base_ytdlp_opts(tmp_path, "720")
    youtube = options["extractor_args"]["youtube"]
    assert youtube["player_client"] == ["default", "mweb"]
    assert youtube["fetch_pot"] == ["always"]
    assert youtube["visitor_data"] == ["shared-binding"]
    assert youtube["player_skip"] == ["webpage", "configs"]
    assert options["js_runtimes"] == {"node": {}}
    assert "height<=720" in options["format"]


def test_browser_po_token_provider_is_installed_and_uses_wrapper(monkeypatch):
    monkeypatch.setenv("WPC_BROWSER_BIN", "/usr/local/bin/test-chromium-wpc")
    args = _provider_extractor_args()
    assert importlib.metadata.version("yt-dlp-getpot-wpc") == "1.0.0"
    assert args["youtubepot-wpc"]["browser_path"] == ["/usr/local/bin/test-chromium-wpc"]


def test_browser_po_token_provider_defaults_to_safe_wrapper(monkeypatch):
    monkeypatch.delenv("WPC_BROWSER_BIN", raising=False)
    args = _provider_extractor_args()
    assert args["youtubepot-wpc"]["browser_path"] == ["/usr/local/bin/chromium-wpc"]


def test_shorts_url_is_canonicalized_to_watch_url():
    assert _canonical_youtube_url(
        "https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu"
    ) == "https://www.youtube.com/watch?v=T7DA7VTZ_Zs"
    assert _canonical_youtube_url(
        "https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce"
    ) == "https://www.youtube.com/watch?v=kTRs4ycFp_o"


def test_youtube_uses_wpc_before_cdp_after_primary_provider_fails(monkeypatch, tmp_path: Path):
    extractor = UniversalExtractor(use_browser=True)
    output = tmp_path / "wpc.mp4"
    output.write_bytes(b"media")

    monkeypatch.setattr(
        extractor,
        "_download_ytdlp",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("youtube_fragment_forbidden")),
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
        extractor,
        "discover",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CDP should not run")),
    )

    result = extractor.download("https://youtu.be/kTRs4ycFp_o", tmp_path, "best")
    assert result["path"] == str(output)


def test_youtube_403_skips_ytdlp_discovery_and_uses_only_live_cdp_candidates(
    monkeypatch,
    tmp_path: Path,
):
    extractor = UniversalExtractor(use_browser=True)
    youtube_url = "https://youtu.be/kTRs4ycFp_o"

    def blocked(*args, **kwargs):
        raise DownloadError("youtube_fragment_forbidden")

    monkeypatch.setattr(extractor, "_download_ytdlp", blocked)
    monkeypatch.setattr(extractor, "_download_yt_fallback", blocked)
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
