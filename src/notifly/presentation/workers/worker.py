"""ARQ worker definition and CLI entrypoint for NotiFly.

Runs the three worker concerns from docs/architecture.md:

- the **outbox relay** (cron): pushes PENDING outbox events onto the queue
- the **scheduled/retry poller** (cron): releases due scheduled notifications
  and seeds retry events for due deliveries
- the **dispatch job** handler: executes the delivery plan per notification

Start it with the installed console script::

    notifly-worker

or via ARQ directly::

    arq notifly.presentation.workers.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.worker import run_worker

from notifly.config import Settings, get_settings
from notifly.logging import configure_logging
from notifly.presentation.workers.context import WorkerContext
from notifly.presentation.workers.jobs import (
    dispatch_notification,
    relay_outbox,
    release_scheduled,
    requeue_retries,
)

_JOB_FUNCTIONS = (dispatch_notification, relay_outbox, release_scheduled, requeue_retries)


def _seconds_set(interval_seconds: float) -> set[int]:
    """Map a poll interval (s) to the set of seconds it fires on each minute."""
    step = max(1, int(interval_seconds))
    return set(range(0, 60, step))


async def _startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)
    ctx["services"] = WorkerContext(settings, ctx["redis"])


async def _shutdown(ctx: dict[str, Any]) -> None:
    services: WorkerContext = ctx["services"]
    await services.dispose()


def create_worker_settings(settings: Settings | None = None) -> dict[str, Any]:
    """Build the ARQ worker kwargs for ``settings`` (module ``WorkerSettings``)."""
    settings = settings or get_settings()
    return {
        "functions": list(_JOB_FUNCTIONS),
        "cron_jobs": [
            cron(
                relay_outbox,
                name="relay_outbox",
                second=_seconds_set(settings.outbox_poll_interval),
                run_at_startup=True,
            ),
            cron(
                release_scheduled,
                name="release_scheduled",
                second=_seconds_set(settings.scheduled_poll_interval),
                run_at_startup=True,
            ),
            cron(
                requeue_retries,
                name="requeue_retries",
                second=_seconds_set(settings.retry_poll_interval),
                run_at_startup=True,
            ),
        ],
        "redis_settings": RedisSettings.from_dsn(settings.redis_url),
        "on_startup": _startup,
        "on_shutdown": _shutdown,
    }


WorkerSettings = create_worker_settings()


def main() -> None:
    """Run the worker process (console-script entrypoint)."""
    run_worker(WorkerSettings)
