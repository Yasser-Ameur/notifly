"""Tests for the ARQ-backed task dispatcher."""

from __future__ import annotations

from typing import Any

from notifly.infrastructure.redis.tasks import ArqTaskDispatcher


class _FakeArqRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue_job(self, function: str, **kwargs: Any) -> object:
        self.jobs.append((function, dict(kwargs)))
        return object()


async def test_enqueue_delegates_task_name_and_payload() -> None:
    redis = _FakeArqRedis()
    dispatcher = ArqTaskDispatcher(redis)  # type: ignore[arg-type]

    await dispatcher.enqueue("dispatch_notification", {"notification_id": "n-1"})

    assert redis.jobs == [("dispatch_notification", {"notification_id": "n-1"})]


async def test_enqueue_with_empty_payload() -> None:
    redis = _FakeArqRedis()
    dispatcher = ArqTaskDispatcher(redis)  # type: ignore[arg-type]

    await dispatcher.enqueue("relay_outbox", {})

    assert redis.jobs == [("relay_outbox", {})]
