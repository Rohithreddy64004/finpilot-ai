"""Small process-local sliding-window request limit for costly inference routes."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from fastapi import Depends, HTTPException, Request

from backend.app.config import Settings, get_settings


class InMemoryRateLimiter:
    """Bounded rolling request timestamps, keyed by the ASGI client host."""

    def __init__(self, *, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else now
        cutoff = current - self.window_seconds
        with self._lock:
            timestamps = self._requests.setdefault(key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self.max_requests:
                wait_seconds = max(1, math.ceil(self.window_seconds - (current - timestamps[0])))
                return False, wait_seconds
            timestamps.append(current)
            if len(self._requests) > 10_000:
                stale_keys = [
                    address for address, values in self._requests.items()
                    if not values or values[-1] <= cutoff
                ]
                for address in stale_keys:
                    self._requests.pop(address, None)
            return True, 0


async def enforce_api_rate_limit(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    limiter = getattr(request.app.state, "api_rate_limiter", None)
    if limiter is None:
        limiter = InMemoryRateLimiter(
            max_requests=settings.api_rate_limit_requests,
            window_seconds=settings.api_rate_limit_window_seconds,
        )
        request.app.state.api_rate_limiter = limiter
    key = request.client.host if request.client is not None else "unknown"
    allowed, retry_after = limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="The request limit was reached. Please wait before asking another question.",
            headers={"Retry-After": str(retry_after)},
        )
