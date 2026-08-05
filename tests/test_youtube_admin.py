from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import youtube_admin


VALID_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-value\n"
).encode()


def test_install_youtube_cookies_validates_and_writes_private_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(youtube_admin.settings, "cookies_dir", str(tmp_path))

    result = youtube_admin.install_youtube_cookies(VALID_COOKIES)

    target = tmp_path / "youtube.txt"
    assert result["configured"] is True
    assert result["cookie_count"] == 1
    assert target.read_bytes() == VALID_COOKIES
    assert target.stat().st_mode & 0o777 == 0o600


def test_install_youtube_cookies_rejects_non_youtube_file(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(youtube_admin.settings, "cookies_dir", str(tmp_path))
    payload = (
        "# Netscape HTTP Cookie File\n"
        ".example.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-value\n"
    ).encode()

    with pytest.raises(ValueError, match="YouTube"):
        youtube_admin.install_youtube_cookies(payload)


def test_remove_uploaded_cookies_does_not_touch_environment_secret(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(youtube_admin.settings, "cookies_dir", str(tmp_path))
    monkeypatch.setenv("YOUTUBE_COOKIES_B64", "environment-secret")
    target = tmp_path / "youtube.txt"
    target.write_bytes(VALID_COOKIES)

    result = youtube_admin.remove_uploaded_youtube_cookies()

    assert result["removed"] is True
    assert not target.exists()
    assert os.environ["YOUTUBE_COOKIES_B64"] == "environment-secret"
