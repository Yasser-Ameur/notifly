"""ARQ-backed task dispatcher: the production transport for the outbox relay.

The database remains the source of truth; this adapter only enqueues jobs
onto the same ARQ queue the worker consumes, using the worker's own Redis
connection pool.
"""

from __future__ import annotations

from typing import Any

from arq.connections import ArqRedis


class ArqTaskDispatcher:
    """Enqueues outbox events as ARQ jobs."""

    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue(self, task: str, payload: dict[str, Any]) -> None:
        await self._redis.enqueue_job(task, **payload)
