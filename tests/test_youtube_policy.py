from app.youtube_policy import (
    BOT_CHECK,
    FRAGMENT_FORBIDDEN,
    LOGIN_REQUIRED,
    RATE_LIMITED,
    TRANSIENT,
    UNSUPPORTED_CLIENT,
    YouTubeCircuit,
    classify_youtube_error,
    youtube_retry_allowed,
)


def test_classifies_rate_limit_and_bot_challenges() -> None:
    assert classify_youtube_error(RuntimeError("HTTP Error 429: Too Many Requests")) == RATE_LIMITED
    assert classify_youtube_error(RuntimeError("Sign in to confirm you’re not a bot")) == BOT_CHECK
    assert classify_youtube_error(RuntimeError("LOGIN_REQUIRED: log in to continue")) == LOGIN_REQUIRED


def test_classifies_signed_fragment_403_separately() -> None:
    error = RuntimeError(
        "ERROR: [generic] https://rr1---sn.example.googlevideo.com/videoplayback?expire=1: "
        "HTTP Error 403: Forbidden"
    )
    assert classify_youtube_error(error) == FRAGMENT_FORBIDDEN


def test_classifies_unsupported_client_and_unknown_network_failures() -> None:
    assert classify_youtube_error(RuntimeError('Skipping unsupported client "tv_embedded"')) == UNSUPPORTED_CLIENT
    assert classify_youtube_error(RuntimeError("connection reset by peer")) == TRANSIENT


def test_retry_policy_stops_bot_and_rate_limit_storms() -> None:
    for classification in (RATE_LIMITED, BOT_CHECK, LOGIN_REQUIRED, UNSUPPORTED_CLIENT):
        assert youtube_retry_allowed(classification, attempt=0) is False
        assert youtube_retry_allowed(classification, attempt=1) is False


def test_fragment_403_allows_one_full_reextraction_only() -> None:
    assert youtube_retry_allowed(FRAGMENT_FORBIDDEN, attempt=0) is True
    assert youtube_retry_allowed(FRAGMENT_FORBIDDEN, attempt=1) is False


def test_transient_failure_allows_two_bounded_retries() -> None:
    assert youtube_retry_allowed(TRANSIENT, attempt=0) is True
    assert youtube_retry_allowed(TRANSIENT, attempt=1) is True
    assert youtube_retry_allowed(TRANSIENT, attempt=2) is False


def test_circuit_opens_blocks_then_allows_one_canary() -> None:
    now = [1000.0]
    store: dict[str, object] = {}
    circuit = YouTubeCircuit(store=store, cooldown_seconds=60, clock=lambda: now[0])

    assert circuit.allow_request() is True
    circuit.record_failure(BOT_CHECK)

    snapshot = circuit.snapshot()
    assert snapshot.state == "open"
    assert snapshot.failure_count == 1
    assert circuit.allow_request() is False

    now[0] = 1061.0
    assert circuit.allow_request() is True
    assert circuit.snapshot().state == "half_open"
    assert circuit.allow_request() is False

    circuit.record_success()
    assert circuit.snapshot().state == "closed"
    assert circuit.allow_request() is True


def test_non_blocking_failure_does_not_open_circuit() -> None:
    circuit = YouTubeCircuit(store={}, cooldown_seconds=60, clock=lambda: 1000.0)
    circuit.record_failure(TRANSIENT)
    assert circuit.snapshot().state == "closed"
