"""Rate limiter port and an in-memory implementation for tests."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Protocol


class RateLimiter(Protocol):
    """Token-bucket style limiter keyed by an arbitrary string."""

    async def acquire(self, key: str, *, limit: int, window_seconds: float) -> bool:
        """Return True if a token was available, False if the limit is exceeded."""
        ...


class InMemoryRateLimiter:
    """Single-process sliding-window limiter. Not for production fan-out."""

    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, bool] = {}

    async def acquire(self, key: str, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        window = self._calls[key]
        while window and now - window[0] >= window_seconds:
            window.popleft()
        if len(window) < limit:
            window.append(now)
            return True
        return False
