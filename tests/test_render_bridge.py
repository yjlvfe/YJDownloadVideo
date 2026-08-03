import json
from pathlib import Path

from app.remote_worker import create_worker_signature, verify_worker_signature


def test_worker_signature_is_bound_to_request_context():
    body = b'{"message":{"text":"https://example.com/video"}}'
    signature = create_worker_signature("secret-token", body, "telegram-update")

    assert verify_worker_signature(
        "secret-token", body, signature, "telegram-update"
    )
    assert not verify_worker_signature(
        "secret-token", body, signature, "telegram-call:sendMessage"
    )


def test_render_exposes_signed_worker_routes():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert '@app.post("/telegram/update"' in source
    assert '@app.post("/telegram/call/{method}"' in source
    assert "verify_worker_signature" in source


def test_vercel_webhook_forwards_updates_to_render():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert "settings.vercel_mode" in source
    assert "forward_telegram_update" in source
    assert '"delivery": "render-2gb"' in source


def test_startup_readiness_does_not_spam_getme_or_logout_by_default():
    source = Path("docker/start-render.sh").read_text(encoding="utf-8")
    readiness = source[source.index("ready=false"):source.index('if [ "$ready" != "true" ]')]

    assert "/getMe" not in readiness
    assert "TELEGRAM_FORCE_LOGOUT_ON_START" in source
    assert 'if [ "${TELEGRAM_FORCE_LOGOUT_ON_START:-0}" = "1" ]' in source


def test_health_probe_does_not_call_getme():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert 'flow.api.call("getMe"' not in source
    assert "flow.api.ready()" in source


def test_vercel_function_duration_targets_real_entrypoint():
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))

    assert "api/index.py" in config["functions"]
    assert "app/main.py" not in config["functions"]
