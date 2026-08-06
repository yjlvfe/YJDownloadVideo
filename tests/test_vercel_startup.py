from __future__ import annotations

import json
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


def test_vercel_stateless_frontend_starts_without_credentials(monkeypatch):
    monkeypatch.setattr(config, "VERCEL_RUNTIME", True)
    for name in ("SECRET_KEY", "TELEGRAM_BOT_TOKEN", "WORKER_SHARED_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VERCEL_URL", "preview.example.vercel.app")

    settings = config.Settings(_env_file=None)

    assert settings.vercel_mode is True
    assert settings.secret_key != "change-me"
    assert settings.telegram_bot_token == ""
    assert settings.worker_shared_secret == ""


def test_vercel_proxies_stateful_routes_directly_to_render():
    deployment = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    rewrites = {
        item["source"]: item["destination"]
        for item in deployment.get("rewrites", [])
    }

    assert rewrites["/api/:path*"] == "https://yjvideodownloads.onrender.com/api/:path*"
    assert rewrites["/download/:path*"] == "https://yjvideodownloads.onrender.com/download/:path*"
    assert rewrites["/webhook/telegram"] == "https://yjvideodownloads.onrender.com/webhook/telegram"


def test_render_defaults_telegram_webhook_to_render_service():
    startup = Path("docker/start-render.sh").read_text(encoding="utf-8")

    assert "https://yjvideodownloads.onrender.com/webhook/telegram" in startup
    assert "https://yj-download-video.vercel.app/webhook/telegram" not in startup
