"""Where job work goes: Redis/arq in production, the API process itself in dev (`FORMWORK_QUEUE=inline`)."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from formwork_core.config import Settings
from formwork_core.logging import get_logger
from formwork_pipeline.job import JobRunner
from formwork_pipeline.webhooks import deliver

log = get_logger("formwork.queue")


class JobQueue(Protocol):
    async def enqueue_job(self, tenant_id: str, job_id: str) -> None: ...

    async def enqueue_webhook(self, tenant_id: str, delivery_id: str) -> None: ...

    async def close(self) -> None: ...


class InlineQueue:
    """Runs jobs as background tasks in this process. No durability: for local development and tests only."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runner: JobRunner | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("inline_task_failed", error=repr(task.exception()))

    async def _run_job(self, tenant_id: str, job_id: str) -> None:
        assert self.runner is not None
        try:
            await self.runner.run_inline(tenant_id, job_id)
        except Exception as exc:
            await self.runner.fail(tenant_id, job_id, f"internal error: {type(exc).__name__}")
            raise

    async def _deliver(self, tenant_id: str, delivery_id: str) -> None:
        assert self.runner is not None
        for attempt in range(3):
            if await deliver(self.runner.engine, self.settings, tenant_id, delivery_id):
                return
            await asyncio.sleep(0.5 * 2**attempt)

    async def enqueue_job(self, tenant_id: str, job_id: str) -> None:
        self._spawn(self._run_job(tenant_id, job_id))

    async def enqueue_webhook(self, tenant_id: str, delivery_id: str) -> None:
        self._spawn(self._deliver(tenant_id, delivery_id))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()


class ArqQueue:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pool: Any = None

    async def _redis(self) -> Any:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self.settings.redis_url))
        return self._pool

    async def enqueue_job(self, tenant_id: str, job_id: str) -> None:
        await (await self._redis()).enqueue_job(
            "advance_job", tenant_id, job_id, _queue_name=self.settings.queue_default
        )

    async def enqueue_webhook(self, tenant_id: str, delivery_id: str) -> None:
        await (await self._redis()).enqueue_job(
            "deliver_webhook",
            tenant_id,
            delivery_id,
            _job_id=f"webhook:{delivery_id}",
            _queue_name=self.settings.queue_default,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
