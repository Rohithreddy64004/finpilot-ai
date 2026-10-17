from backend.app.rate_limit import InMemoryRateLimiter


def test_sliding_window_limits_per_client_and_expires_old_requests() -> None:
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=10)
    assert limiter.check("client-a", now=100) == (True, 0)
    assert limiter.check("client-a", now=101) == (True, 0)
    assert limiter.check("client-a", now=102) == (False, 8)
    assert limiter.check("client-b", now=102) == (True, 0)
    assert limiter.check("client-a", now=111) == (True, 0)


def test_rate_limit_boundary_does_not_count_expired_timestamp() -> None:
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=5)
    assert limiter.check("client", now=10) == (True, 0)
    assert limiter.check("client", now=15) == (True, 0)
