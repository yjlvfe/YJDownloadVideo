from __future__ import annotations

from pathlib import Path

from app.extractor import UniversalExtractor
from app.pot_runtime_patch import _provider_extractor_args


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
