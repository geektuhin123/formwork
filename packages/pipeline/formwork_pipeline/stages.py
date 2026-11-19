"""Merge and validate stages as pure functions over models. The worker persists around them; evals call them directly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from formwork_core.context import JobContext
from formwork_core.models import CanonicalDocument, Extraction, TargetSchema
from formwork_core.telemetry import span
from formwork_extract import PlanCache, RejectedValue
from formwork_merge import MergeResult, SourceInfo, merge
from formwork_validate import Gap, ValidateOptions, ValidationResult, validate_and_repair

from formwork_pipeline.repair import PipelineRepairer


@dataclass
class RunOptions:
    auto_approve_threshold: float = 0.9
    repair: bool = True
    repair_threshold: float = 0.75
    max_repair_attempts: int = 2
    source_priority: list[str] = field(
        default_factory=list
    )  # file ids, document ids or file names, best first

    @classmethod
    def from_job(cls, options: dict[str, Any], default_threshold: float) -> RunOptions:
        return cls(
            auto_approve_threshold=float(options.get("auto_approve_threshold", default_threshold)),
            repair=bool(options.get("repair", True)),
            source_priority=list(options.get("source_priority") or []),
        )


def build_sources(docs: list[dict[str, Any]], options: RunOptions) -> dict[str, SourceInfo]:
    """docs: [{id, filename, doc_class, document_date, file_id}] in upload order."""
    rank = {name: i for i, name in enumerate(options.source_priority)}
    out = {}
    for i, d in enumerate(docs):
        prio = min(
            (rank[k] for k in (d.get("file_id"), d["id"], d.get("filename")) if k in rank), default=1000
        )
        out[d["id"]] = SourceInfo(
            document_id=d["id"],
            doc_class=d.get("doc_class"),
            filename=d.get("filename") or "",
            document_date=d.get("document_date"),
            priority=prio,
            order=i,
        )
    return out


def merge_stage(
    schema: TargetSchema, candidates: list[Extraction], sources: dict[str, SourceInfo], ctx: JobContext
) -> MergeResult:
    with span("stage.merge", tenant_id=ctx.tenant_id, job_id=ctx.job_id, candidates=len(candidates)):
        return merge(schema, candidates, sources)


def rejected_gaps(rejected: list[RejectedValue], merged: MergeResult) -> list[Gap]:
    """A value dropped by grounding leaves a hole worth repairing, if its document maps to exactly one record."""
    doc_records: dict[str, set[str]] = {}
    for key, docs in merged.record_sources.items():
        for d in docs:
            doc_records.setdefault(d, set()).add(key)
    out = []
    for r in rejected:
        keys = doc_records.get(r.document_id, set())
        if len(keys) == 1:
            out.append(Gap(next(iter(keys)), r.field_path, f"value rejected by grounding ({r.reason})"))
    return out


def rekey_repaired(schema: TargetSchema, extractions: list[Extraction]) -> list[Extraction] | None:
    """A document that had no usable key is its own 'unkeyed-' record. If repair recovered the key, give its fields the
    real record key so they can merge with the other sources. Returns None when nothing changed."""
    from formwork_core.paths import record_key
    from formwork_merge import UNKEYED_PREFIX

    by_record: dict[str, dict[str, Extraction]] = {}
    for e in extractions:
        if e.record_key.startswith(UNKEYED_PREFIX):
            by_record.setdefault(e.record_key, {})[e.field_path] = e
    renames = {}
    for key, values in by_record.items():
        parts: list[object] = [
            values[k].normalized_value
            for k in schema.entity_key
            if k in values and values[k].normalized_value
        ]
        if parts and len(parts) == len(schema.entity_key):
            renames[key] = record_key(parts)
    if not renames:
        return None
    return [
        e.model_copy(update={"record_key": renames[e.record_key]}) if e.record_key in renames else e
        for e in extractions
    ]


async def validate_stage(
    schema: TargetSchema,
    merged: MergeResult,
    rejected: list[RejectedValue],
    docs: dict[str, CanonicalDocument],
    ctx: JobContext,
    options: RunOptions,
    plan_cache: PlanCache | None = None,
    sources: dict[str, SourceInfo] | None = None,
) -> ValidationResult:
    opts = ValidateOptions(
        options.auto_approve_threshold, options.repair_threshold, options.max_repair_attempts
    )
    totals = {"repair_attempts": 0, "repaired": 0}
    with span("stage.validate", tenant_id=ctx.tenant_id, job_id=ctx.job_id, fields=len(merged.extractions)):
        for _round in range(2):
            repairer = (
                PipelineRepairer(schema, docs, merged.record_sources, ctx, plan_cache)
                if options.repair
                else None
            )
            result = await validate_and_repair(
                schema, merged.extractions, repairer, opts, rejected_gaps(rejected, merged)
            )
            for k in totals:
                totals[k] += result.stats.get(k, 0)
            rekeyed = rekey_repaired(schema, result.extractions) if options.repair else None
            if rekeyed is None:
                break
            from formwork_core.models import ExtractionStatus

            reset = [
                e.model_copy(update={"status": ExtractionStatus.EXTRACTED})
                if e.status in (ExtractionStatus.APPROVED, ExtractionStatus.NEEDS_REVIEW)
                else e
                for e in rekeyed
            ]
            merged = merge(schema, reset, sources or {})
    result.stats.update(totals)
    result.stats["records"] = len(merged.record_sources)
    result.stats["unkeyed_documents"] = len(merged.unkeyed_documents)
    result.stats["key_aliases"] = merged.key_aliases
    return result
