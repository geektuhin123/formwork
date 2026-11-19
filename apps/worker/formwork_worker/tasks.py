"""arq worker: one task per pipeline stage unit, idempotent and resumable.

`advance_job` asks JobRunner what can run next and enqueues one `run_task` per unit (fan-out per document and field
group). Each `run_task` records its result under (job_id, stage, document_id, field_group) and calls `advance_job`
again. On startup and every few minutes the worker re-advances every job still in flight, so a killed worker's jobs
continue from their last completed task.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from arq import Retry, cron
from arq.connections import RedisSettings
from formwork_core.config import get_settings
from formwork_core.db import app_engine
from formwork_core.db.repo import active_jobs, pending_webhooks
from formwork_core.logging import configure_logging, get_logger
from formwork_core.provider import build_provider
from formwork_core.storage import build_object_store
from formwork_core.telemetry import configure_telemetry
from formwork_pipeline.job import INGEST, JobRunner, TaskRef
from formwork_pipeline.webhooks import deliver

log = get_logger("formwork.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_json, settings.log_level)
    configure_telemetry("formwork-worker", settings.otel_exporter_otlp_endpoint)
    engine = app_engine(settings)
    redis = ctx["redis"]

    async def notify(tenant_id: str, delivery_id: str) -> None:
        await redis.enqueue_job(
            "deliver_webhook",
            tenant_id,
            delivery_id,
            _job_id=f"webhook:{delivery_id}",
            _queue_name=settings.queue_default,
        )

    ctx.update(
        settings=settings,
        engine=engine,
        runner=JobRunner(
            engine, build_object_store(settings), build_provider(settings), settings, notify=notify
        ),
    )
    await resume(ctx)
    log.info("worker_started", queue=settings.worker_queue)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


async def resume(ctx: dict[str, Any]) -> int:
    """Re-advance every in-flight job and re-queue undelivered webhooks (crash recovery and lost-enqueue sweep)."""
    settings = ctx["settings"]
    jobs = await active_jobs(ctx["engine"])
    for tenant_id, job_id in jobs:
        await ctx["redis"].enqueue_job(
            "advance_job", tenant_id, job_id, True, _queue_name=settings.queue_default
        )
    for tenant_id, delivery_id in await pending_webhooks(ctx["engine"]):
        await ctx["redis"].enqueue_job(
            "deliver_webhook",
            tenant_id,
            delivery_id,
            _job_id=f"webhook:{delivery_id}",
            _queue_name=settings.queue_default,
        )
    if jobs:
        log.info("resumed_jobs", count=len(jobs))
    return len(jobs)


async def advance_job(ctx: dict[str, Any], tenant_id: str, job_id: str, resumed: bool = False) -> int:
    runner: JobRunner = ctx["runner"]
    settings = ctx["settings"]
    refs = await runner.pending(tenant_id, job_id)
    # A resumed job gets fresh arq ids: the dead worker's in-progress markers would otherwise block re-queueing.
    suffix = f":r{int(time.time())}" if resumed else ""
    for ref in refs:
        await ctx["redis"].enqueue_job(
            "run_task",
            tenant_id,
            job_id,
            ref.stage,
            ref.document_id,
            ref.field_group,
            _job_id=ref.arq_id(job_id) + suffix,
            _queue_name=settings.queue_heavy if ref.stage == INGEST else settings.queue_default,
        )
    return len(refs)


async def run_task(
    ctx: dict[str, Any], tenant_id: str, job_id: str, stage: str, document_id: str, field_group: str
) -> bool:
    runner: JobRunner = ctx["runner"]
    settings = ctx["settings"]
    try:
        ok = await runner.execute(tenant_id, job_id, TaskRef(stage, document_id, field_group))
    except Exception as exc:  # transient (database, provider outage): let arq retry, then fail the job
        if ctx.get("job_try", 1) >= WorkerSettings.max_tries:
            await runner.fail(
                tenant_id, job_id, f"{stage}: {type(exc).__name__} after {ctx.get('job_try')} tries"
            )
            return False
        log.warning("task_retry", stage=stage, error=type(exc).__name__, attempt=ctx.get("job_try"))
        raise Retry(defer=min(60, 2 ** ctx.get("job_try", 1))) from exc
    if ok:
        await ctx["redis"].enqueue_job("advance_job", tenant_id, job_id, _queue_name=settings.queue_default)
    return ok


async def deliver_webhook(ctx: dict[str, Any], tenant_id: str, delivery_id: str) -> bool:
    if await deliver(ctx["engine"], ctx["settings"], tenant_id, delivery_id):
        return True
    raise Retry(defer=min(900, 5 * 3 ** ctx.get("job_try", 1)))


async def sweep(ctx: dict[str, Any]) -> int:
    return await resume(ctx)


class WorkerSettings:
    functions: ClassVar[list[Any]] = [advance_job, run_task, deliver_webhook]
    cron_jobs: ClassVar[list[Any]] = [
        cron(sweep, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}, run_at_startup=False)
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = get_settings().worker_queue
    max_jobs = get_settings().worker_max_jobs
    job_timeout = get_settings().job_timeout_s
    max_tries = 4
    keep_result = 0
