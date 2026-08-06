from pathlib import Path

import app.extractor as extractor_module
from app.extractor import MediaCandidate, UniversalExtractor, _browser_capture_url, _hint_height
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
