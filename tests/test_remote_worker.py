import json

from app.config import Settings
from app.remote_worker import (
    create_worker_signature,
    verify_worker_signature,
    worker_endpoint,
)


def test_worker_signature_is_context_bound_and_tamper_safe():
    token = "123:secret"
    body = json.dumps({"update_id": 7}, separators=(",", ":")).encode()
    signature = create_worker_signature(token, body, "telegram-update")

    assert verify_worker_signature(token, body, signature, "telegram-update")
    assert not verify_worker_signature(
        token,
        b'{"update_id":8}',
        signature,
        "telegram-update",
    )
    assert not verify_worker_signature(
        token,
        body,
        signature,
        "telegram-call:sendMessage",
    )


def test_worker_endpoint_stays_under_configured_origin():
    assert (
        worker_endpoint("https://worker.example/", "/telegram/update")
        == "https://worker.example/telegram/update"
    )


def test_default_telegram_limit_is_two_billion_bytes():
    configured = Settings(_env_file=None)
    assert configured.telegram_send_limit_bytes == 2_000_000_000
