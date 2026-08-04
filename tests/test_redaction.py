from app.redaction import SafeYtdlpLogger, sanitize_external_error


def test_signed_media_queries_and_url_credentials_are_redacted() -> None:
    text = (
        "ERROR https://user:pass@rr1---sn.example.googlevideo.com/"
        "videoplayback?expire=123&sig=secret HTTP 403"
    )
    sanitized = sanitize_external_error(text, secrets_to_hide=["secret-token"])

    assert "user:pass" not in sanitized
    assert "expire=123" not in sanitized
    assert "sig=secret" not in sanitized
    assert "[REDACTED_QUERY]" in sanitized
    assert "HTTP 403" in sanitized


def test_explicit_secrets_are_never_returned() -> None:
    sanitized = sanitize_external_error(
        "request failed token=abc123 proxy-password",
        secrets_to_hide=["abc123", "proxy-password"],
    )
    assert "abc123" not in sanitized
    assert "proxy-password" not in sanitized
    assert sanitized.count("[REDACTED_SECRET]") == 2


def test_ytdlp_logger_emits_only_sanitized_text() -> None:
    messages: list[tuple[str, str]] = []
    logger = SafeYtdlpLogger(
        sink=lambda level, message: messages.append((level, message)),
        secrets_to_hide=["bot-secret"],
    )

    logger.error(
        "https://rr.example.googlevideo.com/videoplayback?sig=bot-secret returned 403"
    )

    assert messages == [
        (
            "error",
            "https://rr.example.googlevideo.com/videoplayback?[REDACTED_QUERY] returned 403",
        )
    ]
