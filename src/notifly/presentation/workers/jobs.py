"""ARQ job and cron functions for the NotiFly worker.

Every job is a thin adapter over an application service, receiving its
``WorkerContext`` through ARQ's ``ctx`` dict. Jobs never contain business
logic; they translate the transport payload into a service call and return a
small result summary for observability.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from notifly.presentation.workers.context import WorkerContext


async def dispatch_notification(ctx: dict[str, Any], notification_id: str) -> dict[str, Any]:
    """Dispatch every due delivery of ``notification_id`` (enqueued by the relay)."""
    services: WorkerContext = ctx["services"]
    summary = await services.dispatcher().dispatch_notification(UUID(notification_id))
    return {
        "notification_id": str(summary.notification_id),
        "dispatched": summary.dispatched,
        "skipped": summary.skipped,
    }


async def relay_outbox(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron: push PENDING outbox events onto the queue."""
    services: WorkerContext = ctx["services"]
    published = await services.publisher().publish_pending(
        limit=services.settings.outbox_batch_size
    )
    return {"published": published}


async def release_scheduled(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron: release due scheduled notifications as outbox events."""
    services: WorkerContext = ctx["services"]
    released = await services.publisher().publish_due_scheduled(
        limit=services.settings.outbox_batch_size
    )
    return {"released": released}


async def requeue_retries(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron: seed retry events for due, retryable deliveries."""
    services: WorkerContext = ctx["services"]
    queued = await services.publisher().requeue_due_retries(
        limit=services.settings.outbox_batch_size
    )
    return {"queued": queued}
