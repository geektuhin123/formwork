"""Phase 5 acceptance: killing the worker mid-job and restarting completes the job without duplicate LLM calls."""

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest
from formwork_core.config import Settings
from formwork_core.db import TenantRepo, tenant_tx
from formwork_core.db.repo import create_tenant
from formwork_core.models import JobStatus
from formwork_core.provider import FakeProvider
from formwork_core.storage import LocalObjectStore, MemoryObjectStore, file_key
from formwork_ingest import validate_upload
from formwork_pipeline.job import JobRunner
from formwork_schema_compiler import compile_json_schema
from formwork_testkit import synth
from formwork_testkit.schemas import reconciliation_json_schema
from sqlalchemy import text

pytestmark = pytest.mark.db


class Killed(BaseException):
    """Stands in for SIGKILL: not an Exception, so nothing in the pipeline can catch and tidy up."""


class DyingProvider(FakeProvider):
    def __init__(self, die_on_call: int) -> None:
        super().__init__()
        self.die_on_call = die_on_call

    async def complete(self, request):
        if len(self.calls) + 1 == self.die_on_call:
            raise Killed()
        return await super().complete(request)


def order_files():
    o = synth.make_order(1)
    return [
        ("po.xlsx", synth.po_register_xlsx([o])),
        ("inv.pdf", synth.invoice_pdf(o.invoice)),
        ("grn.pdf", synth.scan_pdf(synth.grn_pdf(o), seed=3)),
    ]


async def make_job(engine, store, tenant_id, options=None):
    schema = compile_json_schema(reconciliation_json_schema(), tenant_id)
    async with tenant_tx(engine, tenant_id) as conn:
        repo = TenantRepo(conn, tenant_id)
        schema = await repo.save_schema(schema)
        ids = []
        for name, data in order_files():
            f = validate_upload(name, data, 10**8)
            key = file_key(tenant_id, f.sha256, name.rsplit(".", 1)[-1])
            await store.put(key, data)
            ids.append((await repo.upsert_file(name, f.mime_type, f.sha256, len(data), key))[0]["id"])
        return (await repo.create_job(schema.id, schema.version, ids, options or {}))["id"]


async def calls(engine, tenant_id, job_id):
    async with tenant_tx(engine, tenant_id) as conn:
        rows = (
            await conn.execute(
                text("select purpose, request_hash from llm_calls where job_id = :j"), {"j": job_id}
            )
        ).all()
        status = (await TenantRepo(conn, tenant_id).get_job(job_id))["status"]
    return rows, status


async def test_crash_mid_job_then_restart_completes_without_duplicate_llm_calls(db, admin_db, tenant):
    settings = Settings(env="test")
    # Reference: the same job, uninterrupted, for another tenant (so nothing is shared or deduplicated).
    ref_tenant = (await create_tenant(admin_db, "Reference"))["id"]
    ref_store, ref_provider = MemoryObjectStore(), FakeProvider()
    ref_job = await make_job(db, ref_store, ref_tenant)
    await JobRunner(db, ref_store, ref_provider, settings).run_inline(ref_tenant, ref_job)
    ref_calls, ref_status = await calls(db, ref_tenant, ref_job)
    assert ref_status in ("completed", "needs_review") and len(ref_calls) == len(ref_provider.calls) >= 5

    for die_on in (
        2,
        4,
        len(ref_calls),
    ):  # die during ingest/classify, during extraction, and at the very last call
        t = (await create_tenant(admin_db, f"Crash {die_on}"))["id"]
        store = MemoryObjectStore()
        job = await make_job(db, store, t)
        dying = DyingProvider(die_on)
        with pytest.raises(Killed):
            await JobRunner(db, store, dying, settings).run_inline(t, job)
        _, status = await calls(db, t, job)
        assert (
            not JobStatus(status).terminal and status != "needs_review"
        )  # left mid-flight, as a killed worker would
        survivor = FakeProvider()
        await JobRunner(db, store, survivor, settings).run_inline(t, job)  # the restarted worker
        rows, status = await calls(db, t, job)
        assert status == ref_status
        assert len(rows) == len(ref_calls) and len({h for _, h in rows}) == len(rows)
        # every paid call was made exactly once across both worker lifetimes
        assert len(dying.calls) + len(survivor.calls) == len(ref_calls), die_on
        async with tenant_tx(db, t) as conn:
            done = [x for x in await TenantRepo(conn, t).tasks(job) if x["status"] == "done"]
        assert len(done) == 3 + 3 + 3  # 3 ingest + 3 extract (one field group) + merge/validate/output


async def test_completed_task_keys_are_skipped_and_zero_retention_purges_sources(db, admin_db):
    settings = Settings(env="test")
    t = (await create_tenant(admin_db, "Zero", settings={"retention": "zero"}))["id"]
    store, provider = MemoryObjectStore(), FakeProvider()
    job = await make_job(db, store, t, {"auto_approve_threshold": 0.0})
    runner = JobRunner(db, store, provider, settings)
    await runner.run_inline(t, job)
    n = len(provider.calls)
    from formwork_pipeline.job import TaskRef

    async with tenant_tx(db, t) as conn:
        repo = TenantRepo(conn, t)
        doc = (await repo.job_document_ids(job))[0]
        status = (await repo.get_job(job))["status"]
    assert await runner.pending(t, job) == [] and len(provider.calls) == n
    assert (
        await runner.execute(t, job, TaskRef("extract", doc, "header")) in (True, False)
        and len(provider.calls) == n
    )
    if (
        status == "needs_review"
    ):  # rule-failed fields wait for a reviewer; approve path is covered by the API test
        return
    assert not any(
        "/files/" in k or "/pages/" in k for k in store.objects
    )  # originals and page images are gone
    assert any(k.endswith("lineage.json") for k in store.objects)
    async with tenant_tx(db, t) as conn:
        assert (await conn.execute(text("select count(*) from blocks where text <> ''"))).scalar() == 0
        assert (await conn.execute(text("select count(*) from extractions"))).scalar() > 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.mark.redis
@pytest.mark.slow
async def test_sigkill_real_arq_worker_then_restart(pg, db, admin_db, tmp_path):
    """The same guarantee with real processes: redis-server + `arq` worker, SIGKILL mid-job, start a new worker."""
    if not shutil.which("redis-server"):
        pytest.skip("redis-server not installed")
    from arq import create_pool
    from arq.connections import RedisSettings

    port = _free_port()
    redis = subprocess.Popen(
        ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL
    )
    env = {
        **os.environ,
        "FORMWORK_ENV": "test",
        "FORMWORK_DATABASE_URL": pg.app_url,
        "FORMWORK_REDIS_URL": f"redis://127.0.0.1:{port}/0",
        "FORMWORK_OBJECT_STORE": "local",
        "FORMWORK_LOCAL_STORE_PATH": str(tmp_path / "objects"),
        "FORMWORK_MODEL_PROVIDER": "fake",
        "FORMWORK_WORKER_MAX_JOBS": "1",
    }
    env.pop("ANTHROPIC_API_KEY", None)

    def start_worker() -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-m", "arq", "formwork_worker.tasks.WorkerSettings"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    worker = None
    try:
        settings = Settings(env="test")
        ref_tenant = (await create_tenant(admin_db, "Reference"))["id"]
        ref_store = MemoryObjectStore()
        ref_job = await make_job(db, ref_store, ref_tenant)
        await JobRunner(db, ref_store, FakeProvider(), settings).run_inline(ref_tenant, ref_job)
        ref_calls, ref_status = await calls(db, ref_tenant, ref_job)

        t = (await create_tenant(admin_db, "Killed"))["id"]
        job = await make_job(db, LocalObjectStore(tmp_path / "objects"), t)
        for _ in range(50):
            try:
                pool = await create_pool(RedisSettings(host="127.0.0.1", port=port))
                break
            except Exception:
                await asyncio.sleep(0.1)
        await pool.enqueue_job("advance_job", t, job, _queue_name="formwork:default")
        worker = start_worker()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:  # wait until the job is under way, then pull the plug
            async with tenant_tx(db, t) as conn:
                done = len(await TenantRepo(conn, t).tasks(job))
            if done >= 2:
                break
            await asyncio.sleep(0.05)
        worker.send_signal(signal.SIGKILL)
        worker.wait()
        _, status = await calls(db, t, job)
        killed_mid_flight = status not in ("completed", "needs_review", "failed")
        worker = start_worker()  # startup re-advances every in-flight job
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            rows, status = await calls(db, t, job)
            if status in ("completed", "needs_review", "failed"):
                break
            await asyncio.sleep(0.2)
        await pool.aclose()
        assert status == ref_status, status
        assert len(rows) == len(ref_calls) and len({h for _, h in rows}) == len(rows)
        assert killed_mid_flight, (
            "the worker finished before it could be killed; the test did not exercise recovery"
        )
    finally:
        for proc in (worker, redis):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
