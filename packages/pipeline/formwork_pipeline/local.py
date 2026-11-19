"""In-process pipeline (no database, no queue). The eval runner and tests use it; the worker runs the same stage
functions with persistence between them."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from formwork_core.context import JobContext
from formwork_core.groups import field_groups
from formwork_core.models import Extraction, TargetSchema
from formwork_core.provider import LLMCallRecord, MemorySink, MeteredProvider
from formwork_extract import ExtractionReport, MemoryPlanCache, PlanCache, RejectedValue
from formwork_ingest import ParsedDocument, StoredFile, ingest_file
from formwork_retrieval import classify_document

from formwork_pipeline.extract import extract_group


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
    sem = asyncio.Semaphore(concurrency)
    report = ExtractionReport()

    async def one(p: ParsedDocument, group_name: str) -> ExtractionReport:
        async with sem:
            group = next(g for g in field_groups(schema) if g.name == group_name)
            return await extract_group(schema, p.document, group, ctx, cache)

    # Sheets first and sequentially, so a shared header signature costs one plan call, not one per file.
    for p in docs:
        for g in field_groups(schema):
            report.extend(await one(p, g.name))
    return report
