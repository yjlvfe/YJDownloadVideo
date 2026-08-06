from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app import pot_runtime_patch as youtube_runtime
from app.extractor import DownloadError, UniversalExtractor


@pytest.fixture(autouse=True)
def reset_youtube_circuit():
    youtube_runtime.get_youtube_circuit().record_success()
    yield
    youtube_runtime.get_youtube_circuit().record_success()


def test_provider_uses_one_mweb_client_and_live_provider(monkeypatch):
    monkeypatch.setenv("BGUTIL_PROVIDER_URL", "http://127.0.0.1:5516/")
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "visitor-session-value")

    args = youtube_runtime._provider_extractor_args(authenticated=False)

    assert args["youtube"]["player_client"] == ["mweb"]
    assert args["youtube"]["fetch_pot"] == ["always"]
    assert args["youtube"]["visitor_data"] == ["visitor-session-value"]
    assert args["youtube"]["player_skip"] == ["webpage", "configs"]
    assert args["youtubepot-bgutilhttp"]["base_url"] == ["http://127.0.0.1:5516"]
    assert "http://127.0.0.1:1" not in repr(args)


def test_authenticated_provider_discovers_account_session(monkeypatch):
    monkeypatch.setenv("YOUTUBE_VISITOR_DATA", "must-not-override-account")

    args = youtube_runtime._provider_extractor_args(authenticated=True)

    assert args["youtube"]["player_client"] == ["mweb"]
    assert "player_skip" not in args["youtube"]
    assert "visitor_data" not in args["youtube"]


def test_fallback_uses_web_safari_without_skipping_session_requests():
    args = youtube_runtime._fallback_extractor_args()

    assert args == {
        "youtube": {
            "player_client": ["web_safari"],
            "fetch_pot": ["never"],
        }
    }


def test_extractor_accepts_proxy_and_wires_cookie_proxy_and_provider(tmp_path: Path):
    assert "proxy_url" in inspect.signature(UniversalExtractor).parameters

    extractor = UniversalExtractor(
        cookies_file="/run/secrets/youtube-cookies.txt",
        proxy_url="http://proxy.example:8080",
        use_browser=False,
    )
    extractor._runtime_url = "https://www.youtube.com/watch?v=kTRs4ycFp_o"

    options = extractor._base_ytdlp_opts(tmp_path, "720")

    assert options["cookiefile"] == "/run/secrets/youtube-cookies.txt"
    assert options["proxy"] == "http://proxy.example:8080"
    assert options["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert "player_skip" not in options["extractor_args"]["youtube"]
    assert options["js_runtimes"] == {"node": {}}
    assert "height<=720" in options["format"]


def test_transient_operation_retries_twice_then_succeeds():
    attempts: list[int] = []
    sleeps: list[float] = []

    def operation():
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise RuntimeError("connection reset by peer")
        return "ok"

    result = youtube_runtime._run_with_youtube_retries(operation, sleep=sleeps.append)

    assert result == "ok"
    assert attempts == [1, 2, 3]
    assert sleeps == [1.0, 2.0]


def test_youtube_download_uses_only_primary_then_one_fallback(monkeypatch, tmp_path: Path):
    extractor = UniversalExtractor(use_browser=False)
    output = tmp_path / "video.mp4"
    output.write_bytes(b"media")
    attempts: list[str] = []

    def fake_attempt(self, url, outdir, preferred, mode):
        attempts.append(mode)
        if mode == "primary":
            raise DownloadError("youtube_fragment_forbidden")
        return {
            "path": str(output),
            "title": "sample",
            "duration": 10,
            "thumbnail": "",
            "ext": "mp4",
        }

    monkeypatch.setattr(youtube_runtime, "_download_youtube_attempt", fake_attempt)

    result = extractor.download("https://youtu.be/kTRs4ycFp_o", tmp_path, "best")

    assert result["path"] == str(output)
    assert attempts == ["primary", "fallback"]


def test_rate_limit_does_not_trigger_fallback_storm(monkeypatch, tmp_path: Path):
    extractor = UniversalExtractor(use_browser=False)
    attempts: list[str] = []

    def rate_limited(self, url, outdir, preferred, mode):
        attempts.append(mode)
        raise DownloadError("youtube_rate_limited")

    monkeypatch.setattr(youtube_runtime, "_download_youtube_attempt", rate_limited)

    with pytest.raises(DownloadError, match="youtube_rate_limited"):
        extractor.download("https://youtu.be/kTRs4ycFp_o", tmp_path, "best")

    assert attempts == ["primary"]


def test_youtube_urls_are_canonicalized():
    assert youtube_runtime._canonical_youtube_url(
        "https://youtube.com/shorts/T7DA7VTZ_Zs?si=abc"
    ) == "https://www.youtube.com/watch?v=T7DA7VTZ_Zs"
    assert youtube_runtime._canonical_youtube_url(
        "https://youtu.be/kTRs4ycFp_o?si=abc"
    ) == "https://www.youtube.com/watch?v=kTRs4ycFp_o"
