"""Asynchronous task dispatch port (ARQ transport in production)."""

from __future__ import annotations

from typing import Any, Protocol


class TaskDispatcher(Protocol):
    """Enqueues jobs for the worker. The database remains the source of truth;
    the dispatcher is only a transport."""

    async def enqueue(self, task: str, payload: dict[str, Any]) -> None: ...


class InMemoryTaskDispatcher:
    """Records enqueued jobs in memory. For tests and single-process runs."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, task: str, payload: dict[str, Any]) -> None:
        self.jobs.append((task, payload))
