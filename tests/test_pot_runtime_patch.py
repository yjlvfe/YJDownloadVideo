from __future__ import annotations

from pathlib import Path

from app.extractor import UniversalExtractor
from app.pot_runtime_patch import _provider_extractor_args


def test_po_token_provider_uses_local_service_and_mweb(monkeypatch):
    monkeypatch.setenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:5516/")
    args = _provider_extractor_args()
    assert args["youtube"]["player_client"] == ["mweb"]
    assert args["youtube"]["fetch_pot"] == ["always"]
    assert args["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:5516"]


def test_base_options_include_provider_and_node_runtime(tmp_path: Path):
    extractor = UniversalExtractor(use_browser=False)
    options = extractor._base_ytdlp_opts(tmp_path, "720")
    assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert options["extractor_args"]["youtube"]["fetch_pot"] == ["always"]
    assert options["js_runtimes"] == {"node": {}}
    assert "height<=720" in options["format"]
