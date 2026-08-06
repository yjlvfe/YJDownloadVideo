from __future__ import annotations

from pathlib import Path

from app import config


def test_vercel_can_derive_signing_key_from_worker_secret(monkeypatch):
    monkeypatch.setattr(config, "VERCEL_RUNTIME", True)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("WORKER_SHARED_SECRET", "dedicated-worker-secret")

    settings = config.Settings(_env_file=None)

    assert settings.vercel_mode is True
    assert settings.worker_shared_secret == "dedicated-worker-secret"
    assert settings.secret_key != "change-me"


def test_render_defaults_telegram_webhook_to_render_service():
    startup = Path("docker/start-render.sh").read_text(encoding="utf-8")

    assert "https://yjvideodownloads.onrender.com/webhook/telegram" in startup
    assert "https://yj-download-video.vercel.app/webhook/telegram" not in startup
