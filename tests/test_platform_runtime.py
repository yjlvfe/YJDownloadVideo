import base64
from pathlib import Path

from app import config
from app.extractor import UniversalExtractor
from app.platform_runtime import install_runtime_patches


install_runtime_patches()


def test_ffmpeg_location_returns_absolute_discovered_binary(monkeypatch):
    monkeypatch.setattr(config.settings, "ffmpeg_bin", "ffmpeg")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    assert config.ffmpeg_location() == "/usr/bin/ffmpeg"


def test_cookie_file_for_url_materializes_domain_secret(tmp_path: Path, monkeypatch):
    payload = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\ttest\n"
    monkeypatch.setattr(config.settings, "cookies_dir", str(tmp_path))
    monkeypatch.setenv("YOUTUBE_COOKIES_B64", base64.b64encode(payload).decode())
    monkeypatch.delenv("INSTAGRAM_COOKIES_B64", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_B64", raising=False)

    cookie_file = config.cookie_file_for_url("https://www.youtube.com/watch?v=test")

    assert cookie_file is not None
    assert Path(cookie_file).read_bytes() == payload
    assert Path(cookie_file).stat().st_mode & 0o777 == 0o600
    assert config.cookie_file_for_url("https://www.instagram.com/reel/test") is None


def test_ytdlp_options_use_absolute_ffmpeg_and_proven_youtube_clients(monkeypatch, tmp_path: Path):
    binaries = {"ffmpeg": "/usr/bin/ffmpeg", "node": "/usr/bin/node"}
    monkeypatch.setattr("shutil.which", lambda name: binaries.get(name))
    monkeypatch.setattr(config.settings, "ffmpeg_bin", "ffmpeg")

    opts = UniversalExtractor()._base_ytdlp_opts(tmp_path, "720")

    assert opts["ffmpeg_location"] == "/usr/bin/ffmpeg"
    assert opts["js_runtimes"] == {"node": {}}
    # 🎯 الإصلاح المجرّب: عميل android يتجاوز حجب يوتيوب — بدونها LOGIN_REQUIRED
    clients = opts.get("extractor_args", {}).get("youtube", {}).get("player_client", [])
    assert "android" in clients
    # لا انتحال شخصية غير مدعومة (يكسر yt-dlp >= 2026)
    assert "impersonate" not in opts
