from pathlib import Path

import pytest

from app.config import Settings


ROOT = Path(__file__).resolve().parents[1]
PINNED_YTDLP = "yt-dlp==2026.7.23.234303.dev0"


def test_runtime_requirements_pin_the_tested_ytdlp_version() -> None:
    requirements = (
        ROOT / "requirements-runtime.txt"
    ).read_text(encoding="utf-8").splitlines()
    assert PINNED_YTDLP in requirements
    assert not any(line.startswith("yt-dlp>=") for line in requirements)


def test_render_startup_never_mutates_python_dependencies() -> None:
    startup = (ROOT / "docker" / "start-render.sh").read_text(encoding="utf-8")
    assert "pip install" not in startup
    assert "python -m pip" not in startup


def test_runtime_defaults_are_safe_and_observable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    monkeypatch.setenv("WORKER_SHARED_SECRET", "worker-secret")
    monkeypatch.setenv("YOUTUBE_PROXY_URL", "http://proxy.example:8080")
    monkeypatch.setenv("ENABLE_PUBLIC_YOUTUBE_FALLBACK", "0")
    monkeypatch.delenv("FILE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("MAX_DURATION_SECONDS", raising=False)
    monkeypatch.delenv("DOWNLOAD_TIMEOUT_SECONDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.git_sha == "deadbeef"
    assert settings.worker_shared_secret == "worker-secret"
    assert settings.youtube_proxy_url == "http://proxy.example:8080"
    assert settings.enable_public_youtube_fallback is False
    assert settings.file_ttl_seconds == 3600
    assert settings.max_duration_seconds == 0
    assert settings.download_timeout_seconds == 21600
    assert settings.max_active_jobs_per_ip == 2
    assert settings.max_active_jobs_per_telegram_user == 2
    assert "worker-secret" not in repr(settings)


def test_runtime_derives_sha_from_render_or_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "render-sha")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "vercel-sha")
    assert Settings(_env_file=None).git_sha == "render-sha"

    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    assert Settings(_env_file=None).git_sha == "vercel-sha"


def test_deployed_runtime_derives_non_default_secret_from_preserved_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-that-is-not-a-real-telegram-token")

    first = Settings(_env_file=None)
    second = Settings(_env_file=None)

    assert first.secret_key != "change-me"
    assert first.secret_key == second.secret_key
    assert first.telegram_bot_token not in first.secret_key
    assert first.telegram_bot_token not in repr(first)


def test_production_rejects_the_default_signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "change-me")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(_env_file=None)
