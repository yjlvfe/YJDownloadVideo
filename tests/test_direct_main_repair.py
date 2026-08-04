from pathlib import Path
from types import SimpleNamespace

import app.extractor as extractor_module
from app.extractor import DownloadError, MediaCandidate, UniversalExtractor, _browser_capture_url, _hint_height
from app.platform_runtime import _trusted_googlevideo_candidate
from scripts.acceptance_media import absolute_download_url


def test_acceptance_absolutizes_relative_download_url():
    assert absolute_download_url(
        "https://worker.example", "/download/token"
    ) == "https://worker.example/download/token"


def test_acceptance_preserves_absolute_download_url():
    assert absolute_download_url(
        "https://worker.example", "https://cdn.example/file.mp4"
    ) == "https://cdn.example/file.mp4"


def test_youtube_browser_capture_uses_autoplay_embed():
    assert _browser_capture_url(
        "https://youtube.com/shorts/T7DA7VTZ_Zs?feature=share"
    ) == (
        "https://www.youtube.com/embed/T7DA7VTZ_Zs"
        "?autoplay=1&playsinline=1&controls=1"
    )
    assert _browser_capture_url("https://vm.tiktok.com/example/") == (
        "https://vm.tiktok.com/example/"
    )


def test_googlevideo_requires_live_browser_capture_not_cookie_file():
    trusted = SimpleNamespace(source="cdp", cookies_file="")
    trusted_dom = SimpleNamespace(source="cdp-dom", cookies_file="")
    untrusted = SimpleNamespace(source="js-blob", cookies_file="")
    assert _trusted_googlevideo_candidate(trusted) is True
    assert _trusted_googlevideo_candidate(trusted_dom) is True
    assert _trusted_googlevideo_candidate(untrusted) is False


def test_youtube_download_continues_to_browser_after_ytdlp_block(monkeypatch, tmp_path):
    media_path = tmp_path / "captured.mp4"
    media_path.write_bytes(b"media")
    candidate = MediaCandidate(
        "https://example.invalid/captured.mp4",
        "direct",
        "cdp",
        cookies_file="/tmp/browser-cookies.txt",
    )
    extractor = UniversalExtractor(use_browser=False)

    monkeypatch.setattr(
        extractor,
        "_download_ytdlp",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("youtube_circuit_open")),
    )
    monkeypatch.setattr(
        extractor,
        "_download_yt_fallback",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("fallback disabled")),
    )
    monkeypatch.setattr(
        extractor,
        "discover",
        lambda *args, **kwargs: {"title": "captured", "candidates": [candidate]},
    )
    monkeypatch.setattr(
        extractor,
        "_download_candidate",
        lambda *args, **kwargs: {
            "path": str(media_path),
            "title": "captured",
            "duration": 1,
            "thumbnail": "",
            "ext": "mp4",
        },
    )
    monkeypatch.setattr(extractor_module, "_has_audio", lambda path: True)
    monkeypatch.setattr(extractor_module, "_has_video", lambda path: True)

    result = extractor.download(
        "https://youtube.com/shorts/T7DA7VTZ_Zs",
        tmp_path,
        "best",
    )
    assert result["path"] == str(media_path)


def test_youtube_discover_prefers_browser_session_candidates(monkeypatch):
    extractor = UniversalExtractor(use_browser=True)
    html_candidate = MediaCandidate(
        "https://r.example.googlevideo.com/videoplayback?itag=18",
        "direct",
        "js-blob",
    )
    browser_candidate = MediaCandidate(
        "https://r.example.googlevideo.com/videoplayback?itag=22",
        "direct",
        "cdp",
        cookies_file="/tmp/browser-cookies.txt",
    )
    monkeypatch.setattr(
        extractor,
        "_ytdlp_info",
        lambda *args, **kwargs: (_ for _ in ()).throw(DownloadError("blocked")),
    )
    monkeypatch.setattr(
        extractor,
        "_scan_html",
        lambda *args, **kwargs: ([html_candidate], {"title": "video", "thumbnail": ""}),
    )
    monkeypatch.setattr(
        extractor,
        "_capture_browser",
        lambda *args, **kwargs: [browser_candidate],
    )

    result = extractor.discover("https://youtube.com/watch?v=kTRs4ycFp_o")
    assert result["source"] == "browser+html"
    assert result["candidates"][0] is browser_candidate
    assert result["candidates"][1] is html_candidate


def test_itag_height_mapping_is_used_for_browser_candidates():
    assert _hint_height("https://r.example.googlevideo.com/videoplayback?itag=22") == 720
    assert _hint_height("https://r.example.googlevideo.com/videoplayback?itag=137") == 1080


def test_direct_stream_is_consumed_before_context_closes(monkeypatch, tmp_path):
    class Response:
        headers = {"content-type": "video/mp4", "content-length": "16"}
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def raise_for_status(self):
            return None

        def iter_bytes(self, size):
            assert self.closed is False
            yield b"\x00\x00\x00\x18ftypisom0000"

    response = Response()
    monkeypatch.setattr(extractor_module.httpx, "stream", lambda *args, **kwargs: response)
    candidate = MediaCandidate(
        "https://cdn.example/video.mp4", "direct", "cdp", title="video"
    )
    extractor = UniversalExtractor(use_browser=False)
    result = extractor._download_candidate(candidate, tmp_path, "https://youtube.com/watch?v=x")
    assert Path(result["path"]).read_bytes().startswith(b"\x00\x00\x00\x18ftyp")
