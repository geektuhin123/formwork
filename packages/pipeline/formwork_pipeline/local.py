"""In-process pipeline (no database, no queue). The eval runner and tests use it; the worker runs the same stage
functions with persistence between them."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from formwork_core.context import JobContext
from formwork_core.errors import BudgetExceeded
from formwork_core.groups import field_groups
from formwork_core.models import Extraction, TargetSchema
from formwork_core.provider import LLMCallRecord, MemorySink, MeteredProvider
from formwork_extract import ExtractionReport, MemoryPlanCache, PlanCache, RejectedValue
from formwork_ingest import ParsedDocument, StoredFile, ingest_file
from formwork_retrieval import classify_document

from formwork_pipeline.extract import extract_group
from formwork_pipeline.stages import RunOptions, build_sources, merge_stage, validate_stage


@dataclass
class LocalResult:
    documents: list[ParsedDocument]
    candidates: list[Extraction]
    rejected: list[RejectedValue]
    extractions: list[Extraction] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    calls: list[LLMCallRecord] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def pages(self) -> int:
        return sum(max(1, d.document.page_count) for d in self.documents)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)


def metered(ctx: JobContext) -> tuple[JobContext, MemorySink]:
    """Wrap the context's provider so every call is logged, cached per job and budget-checked."""
    sink = MemorySink()
    provider = MeteredProvider(ctx.provider, sink, ctx.tenant_id, ctx.job_id, ctx.budget)
    return JobContext(
        tenant_id=ctx.tenant_id,
        job_id=ctx.job_id,
        provider=provider,
        store=ctx.store,
        settings=ctx.settings,
        embedder=ctx.embedder,
        budget=ctx.budget,
    ), sink


async def ingest_and_classify(files: list[StoredFile], ctx: JobContext) -> list[ParsedDocument]:
    docs: list[ParsedDocument] = []
    for f in files:
        for p in await ingest_file(f, ctx):
            with contextlib.suppress(BudgetExceeded):  # unclassified is fine; extraction will stop on its own
                p.document.doc_class = await classify_document(p.document, ctx)
            docs.append(p)
    return docs


async def extract_all(
    schema: TargetSchema,
    docs: list[ParsedDocument],
    ctx: JobContext,
    plan_cache: PlanCache | None = None,
    concurrency: int = 4,
) -> ExtractionReport:
    cache = plan_cache or MemoryPlanCache()
    report = ExtractionReport()
    # Sequential on purpose: a shared header signature then costs one plan call, not one per file.
    for p in docs:
        for g in field_groups(schema):
            try:
                report.extend(await extract_group(schema, p.document, g, ctx, cache))
            except BudgetExceeded:  # out of budget: keep what we have; the caller reports a partial result
                report.budget_exhausted = True
                return report
    return report


async def run_local(
    schema: TargetSchema, files: list[StoredFile], ctx: JobContext, options: RunOptions | None = None
) -> LocalResult:
    """ingest -> classify -> extract -> merge -> validate/repair, in memory."""
    opts = options or RunOptions()
    mctx, sink = metered(ctx)
    cache = MemoryPlanCache()
    docs = await ingest_and_classify(files, mctx)
    report = await extract_all(schema, docs, mctx, cache)
    meta = [
        {
            "id": p.document.id,
            "filename": p.document.filename,
            "doc_class": p.document.doc_class,
            "document_date": p.meta.get("document_date"),
        }
        for p in docs
    ]
    sources = build_sources(meta, opts)
    merged = merge_stage(schema, report.extractions, sources, mctx)
    validated = await validate_stage(
        schema, merged, report.rejected, {p.document.id: p.document for p in docs}, mctx, opts, cache, sources
    )
    stats = {**validated.stats, "rejected": len(report.rejected)}
    stats["budget_exhausted"] = bool(stats.get("budget_exhausted") or report.budget_exhausted)
    return LocalResult(
        docs,
        report.extractions,
        report.rejected,
        validated.extractions,
        [g.as_dict() for g in validated.gaps],
        list(sink.records),
        stats,
    )
