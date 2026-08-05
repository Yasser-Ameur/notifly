"""Production rate limiter backed by a Redis sorted set (atomic Lua).

The sliding window keeps a ZSET of per-call timestamps; a single atomic EVAL
prunes the window, counts the remaining calls, and either records a new call
or rejects it. The whole decision is atomic, so concurrent workers share one
limit without a distributed lock.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Final, cast

from redis.asyncio import Redis

_SLIDING_WINDOW_SCRIPT: Final = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window_ms)
local count = redis.call('ZCARD', key)
if count < limit then
  local seq = redis.call('INCR', key .. ':seq')
  redis.call('ZADD', key, now, now .. ':' .. seq)
  redis.call('PEXPIRE', key, window_ms)
  return 1
end
return 0
"""


class RedisRateLimiter:
    """Sliding-window limiter; one limit per distinct key."""

    def __init__(
        self,
        redis: Redis,
        *,
        key_prefix: str = "notifly:ratelimit",
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    async def acquire(self, key: str, *, limit: int, window_seconds: float) -> bool:
        window_ms = int(window_seconds * 1000)
        redis: Any = cast(Any, self._redis)
        allowed: int = await redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            1,
            f"{self._key_prefix}:{key}",
            str(self._now_ms()),
            str(window_ms),
            str(limit),
        )
        return allowed == 1
