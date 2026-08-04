from app.rate_limit import SlidingWindowLimiter


def test_limiter_allows_limit_then_rejects_until_window_expires() -> None:
    now = [1000.0]
    limiter = SlidingWindowLimiter(clock=lambda: now[0])

    assert limiter.allow("inspect:1", limit=2, window_seconds=60)
    assert limiter.allow("inspect:1", limit=2, window_seconds=60)
    assert not limiter.allow("inspect:1", limit=2, window_seconds=60)

    now[0] = 1061.0
    assert limiter.allow("inspect:1", limit=2, window_seconds=60)


def test_limiter_keeps_scopes_and_users_separate() -> None:
    limiter = SlidingWindowLimiter(clock=lambda: 1000.0)
    assert limiter.allow("inspect:user-a", limit=1, window_seconds=60)
    assert not limiter.allow("inspect:user-a", limit=1, window_seconds=60)
    assert limiter.allow("create:user-a", limit=1, window_seconds=60)
    assert limiter.allow("inspect:user-b", limit=1, window_seconds=60)
