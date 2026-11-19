"""Extraction stage for one (document, field group): retrieve -> route -> extract -> combine."""

from __future__ import annotations

from typing import Any

from formwork_core.context import JobContext
from formwork_core.groups import FieldGroup, field_groups
from formwork_core.models import Alternative, BlockType, CanonicalDocument, Extraction, TargetSchema
from formwork_core.provider import ModelTier
from formwork_core.telemetry import span
from formwork_extract import (
    ExtractionReport,
    PlanCache,
    TableExtractor,
    TabularMapper,
    TextExtractor,
    VisionExtractor,
)
from formwork_retrieval import select_blocks
from formwork_router import ExtractorKind, partition

MIN_TEXT_BESIDE_SHEETS = 400  # a sheet's stray title cells are not worth an LLM call


def combine(extractions: list[Extraction]) -> list[Extraction]:
    """Several extractors may answer the same field of one document: keep the most confident, the rest as alternatives."""
    best: dict[tuple[str, str], Extraction] = {}
    for e in sorted(extractions, key=lambda x: (x.normalized_value is not None, x.confidence), reverse=True):
        k = (e.record_key, e.field_path)
        if k not in best:
            best[k] = e
        elif e.normalized_value is not None and e.normalized_value != best[k].normalized_value:
            best[k].alternatives.append(
                Alternative(
                    value=e.value,
                    normalized_value=e.normalized_value,
                    confidence=e.confidence,
                    extractor=e.extractor,
                    evidence=e.evidence,
                )
            )
    return list(best.values())


async def extract_group(
    schema: TargetSchema,
    doc: CanonicalDocument,
    group: FieldGroup,
    ctx: JobContext,
    plan_cache: PlanCache | None = None,
    *,
    tier: ModelTier | None = None,
    feedback: str | None = None,
    widen: bool = False,
    attempt: int = 1,
) -> ExtractionReport:
    report = ExtractionReport()
    with span(
        "stage.extract", tenant_id=ctx.tenant_id, job_id=ctx.job_id, document_id=doc.id, group=group.name
    ):
        blocks = select_blocks(doc, group, ctx.embedder, top_k=ctx.settings.retrieval_top_k, force_all=widen)
        parts = partition(blocks)
        first_group = field_groups(schema)[0].name == group.name
        sheets = [b for b in doc.blocks if b.type is BlockType.SHEET_RANGE]
        if (
            sheets and first_group and attempt == 1
        ):  # one mapping plan covers every field; run it once per document
            mapper = TabularMapper(plan_cache)
            for b in sheets:
                report.extractions.extend(await mapper.extract_block(schema, b, ctx))
        prose = sum(len(b.text) for b in parts[ExtractorKind.TEXT])
        if sheets and prose < MIN_TEXT_BESIDE_SHEETS:
            return report
        kw: dict[str, Any] = {"feedback": feedback, "doc_class": doc.doc_class, "attempt": attempt}
        if group.is_array:
            tables = parts[ExtractorKind.TABLE]
            if tables:
                report.extend(await TableExtractor().run(group, tables, ctx, tier=tier, **kw))
            elif parts[ExtractorKind.TEXT]:
                report.extend(
                    await TextExtractor().run(group, parts[ExtractorKind.TEXT], ctx, tier=tier, **kw)
                )
            scans = [b for b in parts[ExtractorKind.VISION] if b.type is BlockType.TABLE]
            if scans:
                report.extend(await VisionExtractor().run(group, scans, ctx, **kw))
        else:
            readable = sorted(
                parts[ExtractorKind.TEXT] + parts[ExtractorKind.TABLE], key=lambda b: b.reading_order
            )
            if readable:
                report.extend(await TextExtractor().run(group, readable, ctx, tier=tier, **kw))
            if parts[ExtractorKind.VISION]:
                report.extend(await VisionExtractor().run(group, parts[ExtractorKind.VISION], ctx, **kw))
        report.extractions = combine(report.extractions)
    return report
