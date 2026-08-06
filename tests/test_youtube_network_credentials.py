from __future__ import annotations

import inspect
from pathlib import Path

import yt_dlp

from app import worker
from app.extractor import UniversalExtractor
from app.platform_runtime import install_runtime_patches


install_runtime_patches()


def test_worker_uses_platform_cookie_when_owner_cookie_is_missing(monkeypatch, tmp_path: Path):
    assert hasattr(worker, "_cookie_file_for_job"), "worker must resolve platform cookie secrets"

    monkeypatch.setattr(worker.settings, "cookies_dir", str(tmp_path))
    monkeypatch.setattr(
        worker,
        "cookie_file_for_url",
        lambda url: "/run/secrets/youtube-cookies.txt",
        raising=False,
    )

    resolved = worker._cookie_file_for_job(
        "acceptance-owner",
        "https://www.youtube.com/watch?v=kTRs4ycFp_o",
    )

    assert resolved == "/run/secrets/youtube-cookies.txt"


def test_youtube_proxy_reaches_download_and_metadata_options(monkeypatch, tmp_path: Path):
    assert "proxy_url" in inspect.signature(UniversalExtractor).parameters

    proxy_url = "http://proxy-user:proxy-pass@proxy.example:8080"
    monkeypatch.setattr(worker.settings, "youtube_proxy_url", proxy_url)
    extractor = UniversalExtractor(use_browser=False)
    extractor._runtime_url = "https://www.youtube.com/watch?v=kTRs4ycFp_o"

    download_options = extractor._base_ytdlp_opts(tmp_path, "best")
    assert download_options["proxy"] == proxy_url

    captured: dict = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            return {
                "id": "kTRs4ycFp_o",
                "title": "sample",
                "formats": [{"url": "https://example.invalid/video.mp4"}],
            }

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    extractor._ytdlp_info("https://www.youtube.com/watch?v=kTRs4ycFp_o")

    assert captured["proxy"] == proxy_url
