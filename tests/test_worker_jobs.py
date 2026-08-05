"""Tests for the ARQ worker: job glue, settings wiring, and composition root."""

from __future__ import annotations

from uuid import UUID, uuid4

from fakeredis.aioredis import FakeRedis

from notifly.application.dto import DispatchSummary
from notifly.config import Environment, Settings
from notifly.presentation.workers.context import WorkerContext
from notifly.presentation.workers.jobs import (
    dispatch_notification,
    relay_outbox,
    release_scheduled,
    requeue_retries,
)
from notifly.presentation.workers.worker import (
    WorkerSettings,
    _seconds_set,
    create_worker_settings,
)


class _StubDispatcher:
    def __init__(self, summary: DispatchSummary) -> None:
        self._summary = summary
        self.received: UUID | None = None

    async def dispatch_notification(self, notification_id: UUID) -> DispatchSummary:
        self.received = notification_id
        return self._summary


class _StubPublisher:
    def __init__(self, published: int = 0, released: int = 0, queued: int = 0) -> None:
        self._published = published
        self._released = released
        self._queued = queued
        self.limits: list[int] = []

    async def publish_pending(self, *, limit: int) -> int:
        self.limits.append(limit)
        return self._published

    async def publish_due_scheduled(self, *, limit: int) -> int:
        self.limits.append(limit)
        return self._released

    async def requeue_due_retries(self, *, limit: int) -> int:
        self.limits.append(limit)
        return self._queued


class _StubServices:
    def __init__(
        self,
        settings: Settings,
        dispatcher: _StubDispatcher,
        publisher: _StubPublisher,
    ) -> None:
        self.settings = settings
        self._dispatcher = dispatcher
        self._publisher = publisher

    def dispatcher(self) -> _StubDispatcher:
        return self._dispatcher

    def publisher(self) -> _StubPublisher:
        return self._publisher


def _services(**kwargs: object) -> _StubServices:
    return _StubServices(
        Settings(environment=Environment.TEST, **kwargs),
        _StubDispatcher(DispatchSummary(notification_id=uuid4(), dispatched=1)),
        _StubPublisher(),
    )


async def test_dispatch_job_calls_dispatcher_and_returns_summary() -> None:
    notification_id = uuid4()
    services = _services()
    services._dispatcher._summary = DispatchSummary(notification_id=notification_id, dispatched=2)

    result = await dispatch_notification({"services": services}, str(notification_id))

    assert services._dispatcher.received == notification_id
    assert result == {
        "notification_id": str(notification_id),
        "dispatched": 2,
        "skipped": False,
    }


async def test_dispatch_job_reports_skipped() -> None:
    notification_id = uuid4()
    services = _services()
    services._dispatcher._summary = DispatchSummary(notification_id=notification_id, skipped=True)

    result = await dispatch_notification({"services": services}, str(notification_id))

    assert result["skipped"] is True
    assert result["dispatched"] == 0


async def test_relay_job_returns_published_count() -> None:
    services = _services()
    services._publisher._published = 7

    result = await relay_outbox({"services": services})

    assert result == {"published": 7}


async def test_release_scheduled_job_returns_released_count() -> None:
    services = _services()
    services._publisher._released = 3

    result = await release_scheduled({"services": services})

    assert result == {"released": 3}


async def test_requeue_retries_job_returns_queued_count() -> None:
    services = _services()
    services._publisher._queued = 4

    result = await requeue_retries({"services": services})

    assert result == {"queued": 4}


async def test_poller_jobs_use_configured_batch_size() -> None:
    services = _services(outbox_batch_size=25)

    await relay_outbox({"services": services})
    await release_scheduled({"services": services})
    await requeue_retries({"services": services})

    assert services._publisher.limits == [25, 25, 25]


def test_worker_settings_declares_all_jobs() -> None:
    settings = create_worker_settings(Settings(environment=Environment.TEST))

    assert {fn.__name__ for fn in settings["functions"]} == {
        "dispatch_notification",
        "relay_outbox",
        "release_scheduled",
        "requeue_retries",
    }
    assert {job.name for job in settings["cron_jobs"]} == {
        "relay_outbox",
        "release_scheduled",
        "requeue_retries",
    }
    assert all(job.run_at_startup for job in settings["cron_jobs"])
    assert settings["redis_settings"].host == "localhost"
    assert settings["redis_settings"].database == 0


def test_worker_settings_respects_redis_url() -> None:
    settings = create_worker_settings(
        Settings(environment=Environment.TEST, redis_url="redis://cache.internal:6379/3")
    )

    assert settings["redis_settings"].port == 6379
    assert settings["redis_settings"].database == 3


def test_worker_settings_supports_custom_intervals() -> None:
    settings = create_worker_settings(
        Settings(
            environment=Environment.TEST,
            outbox_poll_interval=5,
            scheduled_poll_interval=10,
            retry_poll_interval=20,
        )
    )
    jobs = {job.name: job.second for job in settings["cron_jobs"]}

    assert jobs == {
        "relay_outbox": {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
        "release_scheduled": {0, 10, 20, 30, 40, 50},
        "requeue_retries": {0, 20, 40},
    }


def test_seconds_set_maps_interval() -> None:
    assert _seconds_set(2) == set(range(0, 60, 2))
    assert _seconds_set(15) == {0, 15, 30, 45}
    assert _seconds_set(0.5) == set(range(0, 60))


async def test_module_worker_settings_is_arq_consumable() -> None:
    from arq.worker import create_worker

    worker = create_worker(WorkerSettings)

    assert {"dispatch_notification", "relay_outbox", "release_scheduled", "requeue_retries"} <= set(
        worker.functions
    )
    assert len(worker.cron_jobs) == 3


async def test_worker_context_composition(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}",
    )
    redis = FakeRedis(decode_responses=True)
    context = WorkerContext(settings, redis)  # type: ignore[arg-type]

    limiter = context.rate_limiter()
    assert await limiter.acquire("app:email", limit=1, window_seconds=60.0) is True
    assert await limiter.acquire("app:email", limit=1, window_seconds=60.0) is False

    assert context.publisher() is not None
    assert context.dispatcher() is not None

    await context.dispose()
    await redis.aclose()


async def test_worker_startup_builds_services_and_shutdown_disposes(tmp_path, monkeypatch) -> None:
    from notifly.presentation.workers import worker as worker_module

    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'startup.db'}",
    )
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    redis = FakeRedis(decode_responses=True)

    ctx: dict = {"redis": redis}
    await worker_module._startup(ctx)
    services = ctx["services"]
    assert isinstance(services, WorkerContext)

    await worker_module._shutdown(ctx)
    await redis.aclose()


def test_worker_main_entrypoint_runs_worker(monkeypatch) -> None:
    from notifly.presentation.workers import worker as worker_module

    calls: list[object] = []

    def fake_run_worker(settings, **kwargs: object) -> None:
        calls.append(settings)

    monkeypatch.setattr(worker_module, "run_worker", fake_run_worker)

    worker_module.main()

    assert calls == [worker_module.WorkerSettings]
