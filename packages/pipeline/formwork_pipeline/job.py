"""Database-backed job orchestration.

A job is a sequence of stage tasks keyed by (job_id, stage, document_id, field_group). `pending()` reads the job_tasks
table and returns what can run next; `execute()` runs one task and records its result. A completed key is skipped on
retry, and every LLM call is cached per job by request hash, so a worker killed mid-job resumes from its last completed
stage without paying for the same call twice. The arq worker and the API's inline queue both drive this class.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from formwork_core.config import Settings
from formwork_core.context import JobContext
from formwork_core.db import DbCallSink, TenantRepo, tenant_tx
from formwork_core.embedding import HashingEmbedder
from formwork_core.errors import BudgetExceeded, FormworkError
from formwork_core.groups import field_groups
from formwork_core.logging import bind_context, clear_context, get_logger
from formwork_core.models import Extraction, ExtractionStatus, JobStatus
from formwork_core.provider import Budget, MeteredProvider, ModelProvider
from formwork_core.storage import ObjectStore, lineage_key, output_key
from formwork_core.telemetry import span
from formwork_extract import ExtractionReport, RejectedValue
from formwork_ingest import StoredFile, ingest_file
from formwork_ingest.base import PARSER_VERSION
from formwork_merge import MergeResult
from formwork_output import CONTENT_TYPES, build_lineage, render
from formwork_retrieval import classify_document
from sqlalchemy.ext.asyncio import AsyncEngine

from formwork_pipeline.extract import extract_group
from formwork_pipeline.stages import RunOptions, build_sources, merge_stage, validate_stage
from formwork_pipeline.webhooks import status_payload

log = get_logger("formwork.job")
INGEST, EXTRACT, MERGE, VALIDATE, OUTPUT = "ingest", "extract", "merge", "validate", "output"
_STAGE_STATUS = {
    INGEST: JobStatus.INGESTING,
    EXTRACT: JobStatus.EXTRACTING,
    MERGE: JobStatus.MERGING,
    VALIDATE: JobStatus.VALIDATING,
    OUTPUT: JobStatus.VALIDATING,
}
REVIEW_STATUSES = [
    ExtractionStatus.NEEDS_REVIEW,
    ExtractionStatus.CONFLICT,
    ExtractionStatus.FAILED_VALIDATION,
    ExtractionStatus.EXTRACTED,
]
Notify = Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True)
class TaskRef:
    stage: str
    document_id: str = ""  # the file id for ingest tasks
    field_group: str = ""

    def arq_id(self, job_id: str) -> str:
        return f"{job_id}:{self.stage}:{self.document_id}:{self.field_group}"


class DbPlanCache:
    def __init__(self, engine: AsyncEngine, tenant_id: str) -> None:
        self.engine, self.tenant_id = engine, tenant_id

    async def get(self, schema_id: str, schema_version: int, signature: str) -> dict[str, Any] | None:
        async with tenant_tx(self.engine, self.tenant_id) as conn:
            return await TenantRepo(conn, self.tenant_id).get_plan(schema_id, schema_version, signature)

    async def put(self, schema_id: str, schema_version: int, signature: str, plan: dict[str, Any]) -> None:
        async with tenant_tx(self.engine, self.tenant_id) as conn:
            await TenantRepo(conn, self.tenant_id).put_plan(schema_id, schema_version, signature, plan)


class JobRunner:
    def __init__(
        self,
        engine: AsyncEngine,
        store: ObjectStore,
        provider: ModelProvider,
        settings: Settings,
        notify: Notify | None = None,
    ) -> None:
        self.engine, self.store, self.provider, self.settings, self.notify = (
            engine,
            store,
            provider,
            settings,
            notify,
        )
        self.embedder = HashingEmbedder()

    # ------------------------------------------------------------------ helpers
    def _tx(self, tenant_id: str) -> Any:
        return tenant_tx(self.engine, tenant_id)

    async def _context(self, tenant_id: str, job: dict[str, Any]) -> JobContext:
        async with self._tx(tenant_id) as conn:
            spent = await TenantRepo(conn, tenant_id).refresh_job_cost(job["id"])
        limit = job["options"].get("budget_usd", self.settings.default_budget_usd)
        budget = Budget(float(limit) if limit is not None else None, spent)
        provider = MeteredProvider(self.provider, DbCallSink(self.engine), tenant_id, job["id"], budget)
        return JobContext(
            tenant_id=tenant_id,
            job_id=job["id"],
            provider=provider,
            store=self.store,
            settings=self.settings,
            embedder=self.embedder,
            budget=budget,
        )

    async def _set_status(
        self,
        tenant_id: str,
        job_id: str,
        status: JobStatus,
        error: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        """Change status; on an actual change, queue a signed webhook (fired after the transaction commits)."""
        delivery: str | None = None
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            before = (await repo.get_job(job_id))["status"]
            changed = await repo.set_job_status(job_id, status, error, stats)
            if changed:
                await repo.refresh_job_cost(job_id)
                job = await repo.get_job(job_id)
                url = job["options"].get("webhook_url")
                if url:
                    delivery = await repo.add_delivery(
                        job_id,
                        url,
                        "job.status_changed",
                        status_payload(job, before, datetime.now(UTC).isoformat()),
                    )
        if changed:
            log.info("job_status", job_id=job_id, status=status.value, previous=before)
        if delivery and self.notify:
            with contextlib.suppress(
                Exception
            ):  # a queue hiccup must not fail the stage; the sweeper retries pending rows
                await self.notify(tenant_id, delivery)

    # ------------------------------------------------------------------ planning
    async def pending(self, tenant_id: str, job_id: str) -> list[TaskRef]:
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            job = await repo.get_job(job_id)
            if JobStatus(job["status"]).terminal or job["status"] == JobStatus.NEEDS_REVIEW.value:
                return []
            done = {
                (t["stage"], t["document_id"], t["field_group"])
                for t in await repo.tasks(job_id)
                if t["status"] == "done"
            }
            ingest = [TaskRef(INGEST, f) for f in job["file_ids"] if (INGEST, f, "") not in done]
            if ingest:
                return ingest
            schema = await repo.get_schema(job["schema_id"], job["schema_version"])
            docs = await repo.job_document_ids(job_id)
        extract = [
            TaskRef(EXTRACT, d, g.name)
            for d in docs
            for g in field_groups(schema)
            if (EXTRACT, d, g.name) not in done
        ]
        if extract:
            return extract
        for stage in (MERGE, VALIDATE, OUTPUT):
            if (stage, "", "") not in done:
                return [TaskRef(stage)]
        return []

    async def run_inline(self, tenant_id: str, job_id: str) -> None:
        """Run every remaining task in this process (dev, tests, and the resume path)."""
        while True:
            refs = await self.pending(tenant_id, job_id)
            if not refs:
                return
            for ref in refs:
                if not await self.execute(tenant_id, job_id, ref):
                    return

    # ------------------------------------------------------------------ execution
    async def execute(self, tenant_id: str, job_id: str, ref: TaskRef) -> bool:
        """Run one task. Returns False when the job has failed and nothing further should run."""
        bind_context(tenant_id=tenant_id, job_id=job_id, stage=ref.stage)
        started = time.monotonic()
        try:
            async with self._tx(tenant_id) as conn:
                repo = TenantRepo(conn, tenant_id)
                job = await repo.get_job(job_id)
                existing = await repo.task(job_id, ref.stage, ref.document_id, ref.field_group)
            if JobStatus(job["status"]).terminal:
                return False
            if existing and existing["status"] == "done":
                return True  # idempotent: a completed key is skipped on retry
            await self._set_status(tenant_id, job_id, _STAGE_STATUS[ref.stage])
            ctx = await self._context(tenant_id, job)
            with span(
                f"task.{ref.stage}",
                tenant_id=tenant_id,
                job_id=job_id,
                document_id=ref.document_id,
                group=ref.field_group,
            ):
                handler = {
                    INGEST: self._ingest,
                    EXTRACT: self._extract,
                    MERGE: self._merge,
                    VALIDATE: self._validate,
                    OUTPUT: self._output,
                }[ref.stage]
                result = await handler(ctx, job, ref)
            async with self._tx(tenant_id) as conn:
                await TenantRepo(conn, tenant_id).complete_task(
                    job_id,
                    ref.stage,
                    ref.document_id,
                    ref.field_group,
                    result,
                    int((time.monotonic() - started) * 1000),
                )
            return True
        except FormworkError as exc:  # unrecoverable for this job (bad file, missing schema, ...)
            await self.fail(tenant_id, job_id, f"{ref.stage}: {exc}")
            return False
        finally:
            clear_context()

    async def fail(self, tenant_id: str, job_id: str, error: str) -> None:
        log.error("job_failed", job_id=job_id, error=error)
        await self._set_status(tenant_id, job_id, JobStatus.FAILED, error=error)

    async def _ingest(self, ctx: JobContext, job: dict[str, Any], ref: TaskRef) -> dict[str, Any]:
        tenant_id = ctx.tenant_id
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            file = await repo.get_file(ref.document_id)
            existing = await repo.documents_for_file(file["id"])
        if existing:  # sha256 dedupe: this file was already parsed for the tenant; reuse its blocks
            ids = [d["id"] for d in existing]
            reused = True
        else:
            data = await self.store.get(file["storage_key"])
            parsed = await ingest_file(
                StoredFile(
                    filename=file["filename"], data=data, mime_type=file["mime_type"], sha256=file["sha256"]
                ),
                ctx,
            )
            for p in parsed:
                with contextlib.suppress(BudgetExceeded):
                    p.document.doc_class = await classify_document(p.document, ctx)
            async with self._tx(tenant_id) as conn:
                repo = TenantRepo(conn, tenant_id)
                for p in parsed:
                    await repo.save_document(
                        p.document, file["id"], p.parent_document_id, PARSER_VERSION, p.meta
                    )
            ids = [p.document.id for p in parsed]
            reused = False
        async with self._tx(tenant_id) as conn:
            await TenantRepo(conn, tenant_id).link_documents(job["id"], ids)
        return {"document_ids": ids, "reused": reused}

    async def _extract(self, ctx: JobContext, job: dict[str, Any], ref: TaskRef) -> dict[str, Any]:
        async with self._tx(ctx.tenant_id) as conn:
            repo = TenantRepo(conn, ctx.tenant_id)
            schema = await repo.get_schema(job["schema_id"], job["schema_version"])
            doc = await repo.get_document(ref.document_id, job["id"], with_embeddings=True)
        group = next(g for g in field_groups(schema) if g.name == ref.field_group)
        report = ExtractionReport()
        try:
            report = await extract_group(schema, doc, group, ctx, DbPlanCache(self.engine, ctx.tenant_id))
        except BudgetExceeded:
            report.budget_exhausted = True
        return {
            "extractions": [e.model_dump(mode="json") for e in report.extractions],
            "rejected": [asdict(r) for r in report.rejected],
            "budget_exhausted": report.budget_exhausted,
        }

    async def _merge(self, ctx: JobContext, job: dict[str, Any], ref: TaskRef) -> dict[str, Any]:
        async with self._tx(ctx.tenant_id) as conn:
            repo = TenantRepo(conn, ctx.tenant_id)
            schema = await repo.get_schema(job["schema_id"], job["schema_version"])
            tasks = await repo.tasks(job["id"], EXTRACT)
            ingests = {
                t["document_id"]: t["result"]["document_ids"] for t in await repo.tasks(job["id"], INGEST)
            }
            ordered = [d for f in job["file_ids"] for d in ingests.get(f, [])]
            rows = [await repo.document_row(d) for d in ordered]
        candidates = [Extraction.model_validate(e) for t in tasks for e in t["result"]["extractions"]]
        meta = [
            {
                "id": r["id"],
                "filename": r["filename"],
                "doc_class": r["doc_class"],
                "file_id": r["file_id"],
                "document_date": (r["meta"] or {}).get("document_date"),
            }
            for r in rows
        ]
        sources = build_sources(
            meta, RunOptions.from_job(job["options"], self.settings.auto_approve_threshold)
        )
        merged = merge_stage(schema, candidates, sources, ctx)
        return {
            "extractions": [e.model_dump(mode="json") for e in merged.extractions],
            "record_sources": merged.record_sources,
            "key_aliases": merged.key_aliases,
            "unkeyed_documents": merged.unkeyed_documents,
            "sources": meta,
            "budget_exhausted": any(t["result"].get("budget_exhausted") for t in tasks),
        }

    async def _validate(self, ctx: JobContext, job: dict[str, Any], ref: TaskRef) -> dict[str, Any]:
        async with self._tx(ctx.tenant_id) as conn:
            repo = TenantRepo(conn, ctx.tenant_id)
            schema = await repo.get_schema(job["schema_id"], job["schema_version"])
            merge_task = await repo.task(job["id"], MERGE)
            extract_tasks = await repo.tasks(job["id"], EXTRACT)
            docs = {d: await repo.get_document(d, job["id"]) for d in await repo.job_document_ids(job["id"])}
        assert merge_task is not None
        m = merge_task["result"]
        merged = MergeResult(
            [Extraction.model_validate(e) for e in m["extractions"]],
            m["key_aliases"],
            m["record_sources"],
            m["unkeyed_documents"],
        )
        rejected = [RejectedValue(**r) for t in extract_tasks for r in t["result"]["rejected"]]
        options = RunOptions.from_job(job["options"], self.settings.auto_approve_threshold)
        result = await validate_stage(
            schema,
            merged,
            rejected,
            docs,
            ctx,
            options,
            DbPlanCache(self.engine, ctx.tenant_id),
            build_sources(m["sources"], options),
        )
        stats = {
            **result.stats,
            "rejected": len(rejected),
            "budget_exhausted": bool(result.stats.get("budget_exhausted") or m.get("budget_exhausted")),
        }
        async with self._tx(ctx.tenant_id) as conn:
            repo = TenantRepo(conn, ctx.tenant_id)
            await repo.replace_extractions(job["id"], result.extractions)
            await repo.replace_gaps(job["id"], [g.as_dict() for g in result.gaps])
        return stats

    async def _output(self, ctx: JobContext, job: dict[str, Any], ref: TaskRef) -> dict[str, Any]:
        tenant_id = ctx.tenant_id
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            validate_task = await repo.task(job["id"], VALIDATE)
            tasks = await repo.tasks(job["id"])
            tenant = await repo.tenant()
        stats = dict((validate_task or {}).get("result") or {})
        timings: dict[str, int] = {}
        for t in tasks:
            timings[t["stage"]] = timings.get(t["stage"], 0) + int(t["duration_ms"] or 0)
        stats["stage_ms"] = timings
        await self.write_outputs(tenant_id, job["id"])
        if stats.get("budget_exhausted"):
            status, error = JobStatus.FAILED, "job budget reached; the partial result is available"
        elif stats.get("in_review", 0) > 0:
            status, error = JobStatus.NEEDS_REVIEW, None
        else:
            status, error = JobStatus.COMPLETED, None
        if (tenant["settings"] or {}).get("retention") == "zero" and status is JobStatus.COMPLETED:
            await self.purge_sources(tenant_id, job["id"])
        async with self._tx(
            tenant_id
        ) as conn:  # the task row first, so a crash here cannot re-run the stage after 'completed'
            await TenantRepo(conn, tenant_id).complete_task(job["id"], OUTPUT, result=stats)
        await self._set_status(tenant_id, job["id"], status, error=error, stats=stats)
        return stats

    # ------------------------------------------------------------------ outputs, review, retention
    async def write_outputs(self, tenant_id: str, job_id: str) -> None:
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            job = await repo.get_job(job_id)
            schema = await repo.get_schema(job["schema_id"], job["schema_version"])
            extractions = await repo.list_extractions(job_id)
            gaps = await repo.list_gaps(job_id)
        lineage = build_lineage(
            job_id,
            schema,
            extractions,
            [{k: g[k] for k in ("id", "record_key", "field_path", "reason")} for g in gaps],
        )
        import json

        fmt = schema.output_format
        await self.store.put(
            output_key(tenant_id, job_id, fmt.value),
            render(schema, extractions, fmt, True, lineage),
            CONTENT_TYPES[fmt],
        )
        await self.store.put(
            lineage_key(tenant_id, job_id),
            json.dumps(lineage, ensure_ascii=False).encode(),
            "application/json",
        )

    async def after_review(self, tenant_id: str, job_id: str) -> JobStatus:
        """Called after each review action: a job returns to `completed` once nothing is waiting for review."""
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            job = await repo.get_job(job_id)
            open_items = len(await repo.list_extractions(job_id, REVIEW_STATUSES)) + len(
                await repo.list_gaps(job_id)
            )
            all_items = await repo.list_extractions(job_id)
        stats = {
            "in_review": open_items,
            "auto_approved": (job["stats"] or {}).get("auto_approved", 0),
            "approved": sum(1 for e in all_items if e.status is ExtractionStatus.APPROVED),
        }
        if job["status"] == JobStatus.NEEDS_REVIEW.value and open_items == 0:
            await self.write_outputs(tenant_id, job_id)
            tenant_settings: dict[str, Any] = {}
            async with self._tx(tenant_id) as conn:
                tenant_settings = (await TenantRepo(conn, tenant_id).tenant())["settings"] or {}
            if tenant_settings.get("retention") == "zero":
                await self.purge_sources(tenant_id, job_id)
            await self._set_status(tenant_id, job_id, JobStatus.COMPLETED, stats=stats)
            return JobStatus.COMPLETED
        async with self._tx(tenant_id) as conn:
            await TenantRepo(conn, tenant_id).set_job_status(job_id, JobStatus(job["status"]), stats=stats)
        return JobStatus(job["status"])

    async def purge_sources(self, tenant_id: str, job_id: str) -> None:
        """Zero-retention tenants: originals and page images go when the job completes; lineage quotes stay."""
        async with self._tx(tenant_id) as conn:
            repo = TenantRepo(conn, tenant_id)
            job = await repo.get_job(job_id)
            files = [await repo.get_file(f) for f in job["file_ids"]]
            docs = await repo.job_document_ids(job_id)
            await repo.purge_file_content([f["id"] for f in files])
        for f in files:
            await self.store.delete_prefix(f["storage_key"].rsplit("/", 1)[0])
        for d in docs:
            await self.store.delete_prefix(f"{tenant_id}/documents/{d}")
        log.info("sources_purged", job_id=job_id, files=len(files), documents=len(docs))
