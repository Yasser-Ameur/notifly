"""Tests for the Redis-backed sliding-window rate limiter."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fakeredis.aioredis import FakeRedis

from notifly.infrastructure.redis.rate_limit import RedisRateLimiter


@pytest.fixture()
async def redis() -> AsyncIterator[FakeRedis]:
    client = FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def test_allows_up_to_limit(redis: FakeRedis) -> None:
    limiter = RedisRateLimiter(redis)
    for _ in range(3):
        assert await limiter.acquire("app:email", limit=3, window_seconds=60.0) is True


async def test_rejects_beyond_limit(redis: FakeRedis) -> None:
    limiter = RedisRateLimiter(redis)
    for _ in range(3):
        await limiter.acquire("app:email", limit=3, window_seconds=60.0)
    assert await limiter.acquire("app:email", limit=3, window_seconds=60.0) is False


async def test_window_slides(redis: FakeRedis) -> None:
    clock = [1_000_000]
    limiter = RedisRateLimiter(redis, now_ms=lambda: clock[0])
    assert await limiter.acquire("app:email", limit=1, window_seconds=60.0) is True
    assert await limiter.acquire("app:email", limit=1, window_seconds=60.0) is False
    clock[0] = 1_000_000 + 61_000
    assert await limiter.acquire("app:email", limit=1, window_seconds=60.0) is True


async def test_expired_entries_are_pruned(redis: FakeRedis) -> None:
    clock = [1_000_000]
    limiter = RedisRateLimiter(redis, now_ms=lambda: clock[0])
    for _ in range(3):
        await limiter.acquire("app:email", limit=5, window_seconds=60.0)
    clock[0] = 1_000_000 + 61_000
    await limiter.acquire("app:email", limit=5, window_seconds=60.0)
    remaining = await redis.zcard("notifly:ratelimit:app:email")
    assert remaining == 1


async def test_keys_are_independent(redis: FakeRedis) -> None:
    limiter = RedisRateLimiter(redis)
    assert await limiter.acquire("app-a:email", limit=1, window_seconds=60.0) is True
    assert await limiter.acquire("app-b:email", limit=1, window_seconds=60.0) is True


async def test_key_prefix_scopes_state(redis: FakeRedis) -> None:
    a = RedisRateLimiter(redis, key_prefix="prefix-a")
    b = RedisRateLimiter(redis, key_prefix="prefix-b")
    assert await a.acquire("same", limit=1, window_seconds=60.0) is True
    assert await b.acquire("same", limit=1, window_seconds=60.0) is True
