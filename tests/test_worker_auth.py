from app.remote_worker import (
    create_worker_signature,
    verify_worker_request,
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


def test_legacy_signature_helper_remains_available_during_migration() -> None:
    body = b"legacy"
    signature = create_worker_signature("bot-token", body, "telegram-update")
    assert len(signature) == 64
