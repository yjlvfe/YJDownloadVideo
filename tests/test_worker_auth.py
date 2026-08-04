import pytest

from app import remote_worker
from app.remote_worker import (
    build_worker_auth_headers,
    create_worker_signature,
    verify_worker_request,
    verify_worker_signature,
)


def test_timestamped_signature_accepts_exact_request_once() -> None:
    consumed: set[str] = set()

    def consume(nonce: str, expires_at: float) -> bool:
        if nonce in consumed:
            return False
        consumed.add(nonce)
        return True

    body = b'{"update_id":123}'
    signature = create_worker_signature(
        "shared-secret",
        body,
        "telegram-update",
        timestamp=1000,
        nonce="nonce-1",
    )

    assert verify_worker_request(
        "shared-secret",
        body,
        signature,
        "telegram-update",
        timestamp="1000",
        nonce="nonce-1",
        consume_nonce=consume,
        now=1100,
    )
    assert not verify_worker_request(
        "shared-secret",
        body,
        signature,
        "telegram-update",
        timestamp="1000",
        nonce="nonce-1",
        consume_nonce=consume,
        now=1100,
    )


def test_signature_rejects_tampering_wrong_context_and_stale_time() -> None:
    body = b'{"update_id":123}'
    signature = create_worker_signature(
        "shared-secret",
        body,
        "telegram-update",
        timestamp=1000,
        nonce="nonce-2",
    )
    consume = lambda nonce, expires_at: True

    assert not verify_worker_request(
        "shared-secret",
        b'{"update_id":124}',
        signature,
        "telegram-update",
        timestamp="1000",
        nonce="nonce-2",
        consume_nonce=consume,
        now=1100,
    )
    assert not verify_worker_request(
        "shared-secret",
        body,
        signature,
        "telegram-call:sendMessage",
        timestamp="1000",
        nonce="nonce-2",
        consume_nonce=consume,
        now=1100,
    )
    assert not verify_worker_request(
        "shared-secret",
        body,
        signature,
        "telegram-update",
        timestamp="1000",
        nonce="nonce-2",
        consume_nonce=consume,
        now=1301,
    )
    assert not verify_worker_request(
        "shared-secret",
        body,
        signature,
        "telegram-update",
        timestamp="1500",
        nonce="nonce-2",
        consume_nonce=consume,
        now=1100,
    )


def test_rollout_headers_work_with_old_and_new_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_worker.settings, "telegram_bot_token", "bot-token")
    monkeypatch.setattr(remote_worker.settings, "worker_shared_secret", "shared-secret")
    body = b'{"update_id":321}'

    headers = build_worker_auth_headers(
        body,
        "telegram-update",
        timestamp=1000,
        nonce="rollout-nonce",
    )

    # Old Render reads the historical header and verifies it with the bot token.
    assert verify_worker_signature(
        "bot-token",
        body,
        headers["x-telegram-worker-auth"],
        "telegram-update",
    )

    # New Render reads the separate v2 signature and replay metadata.
    assert verify_worker_request(
        "shared-secret",
        body,
        headers["x-worker-signature-v2"],
        "telegram-update",
        timestamp=headers["x-worker-timestamp"],
        nonce=headers["x-worker-nonce"],
        consume_nonce=lambda nonce, expires_at: True,
        now=1100,
    )
    assert headers["x-worker-version"] == "2"


def test_rollout_v2_signature_never_reuses_legacy_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(remote_worker.settings, "telegram_bot_token", "bot-token")
    monkeypatch.setattr(remote_worker.settings, "worker_shared_secret", "shared-secret")
    body = b"payload"

    headers = build_worker_auth_headers(
        body,
        "media-create",
        timestamp=1000,
        nonce="nonce-v2",
    )

    assert headers["x-worker-signature-v2"] != headers["x-telegram-worker-auth"]
    assert not verify_worker_signature(
        "shared-secret",
        body,
        headers["x-telegram-worker-auth"],
        "media-create",
    )


def test_legacy_signature_helper_remains_available_during_migration() -> None:
    body = b"legacy"
    signature = create_worker_signature("bot-token", body, "telegram-update")
    assert len(signature) == 64
