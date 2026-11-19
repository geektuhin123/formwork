"""Review actions: approve, correct, pick an alternative. Every correction is stored as an eval case."""

from __future__ import annotations

from typing import Any

from formwork_core.db import TenantRepo
from formwork_core.errors import Conflict, ValidationFailed
from formwork_core.models import Alternative, Evidence, Extraction, ExtractionStatus, FieldSpec, TargetSchema
from formwork_core.normalize import normalize, typed
from formwork_feedback import record_correction
from formwork_output import lineage_item

from formwork_api.schemas import ReviewAction


def _leaf(schema: TargetSchema, path: str) -> FieldSpec:
    spec = schema.field(path)
    if spec is None or spec.children:
        raise ValidationFailed(f"{path} is not a single field; correct its child fields instead")
    return spec


def _as_alternative(e: Extraction) -> Alternative:
    return Alternative(
        value=e.value,
        normalized_value=e.normalized_value,
        confidence=e.confidence,
        extractor=e.extractor,
        evidence=e.evidence,
    )


def gap_item(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": g["id"],
        "kind": "gap",
        "record_key": g["record_key"],
        "field": g["field_path"],
        "status": "missing",
        "reason": g["reason"],
        "value": None,
        "confidence": 0.0,
        "evidence": [],
        "alternatives": [],
    }


def field_item(e: Extraction) -> dict[str, Any]:
    return {"kind": "field", **lineage_item(e)}


async def apply_review(
    repo: TenantRepo,
    schema: TargetSchema,
    job_id: str,
    item_id: str,
    action: ReviewAction,
    reviewer: str,
    keep_text: bool = True,
) -> tuple[dict[str, Any], str | None]:
    """-> (updated item, eval_case_id or None)."""
    if item_id.startswith("gap_"):
        if action.action != "correct":
            raise ValidationFailed("a missing field can only be resolved with action 'correct' and a value")
        gap = await repo.resolve_gap(job_id, item_id)
        if gap["resolved_at"] is not None:
            raise Conflict("this field was already resolved")
        spec = _leaf(schema, gap["field_path"])
        norm, err = normalize(action.value, spec)
        if err:
            raise ValidationFailed(err)
        e = Extraction(
            record_key=gap["record_key"],
            field_path=gap["field_path"],
            value=typed(norm, spec),
            normalized_value=norm,
            confidence=1.0,
            evidence=[
                Evidence(
                    block_id="manual",
                    document_id="manual",
                    locator=f"manual:{reviewer}",
                    quote=str(action.value),
                )
            ],
            extractor="human",
            status=ExtractionStatus.APPROVED,
            reason="entered by reviewer",
        )
        e.id = await repo.insert_extraction(job_id, e)
        gap_case = await record_correction(
            repo, schema.id, spec, e.field_path, None, e.value, "review:missing", job_id
        )
        return field_item(e), gap_case

    e = await repo.get_extraction(job_id, item_id)
    spec = _leaf(schema, e.field_path)
    case: str | None = None
    if action.action == "approve":
        if e.normalized_value is None:
            raise Conflict("this value failed validation and cannot be approved as-is; correct it")
        updated = e.model_copy(update={"status": ExtractionStatus.APPROVED, "reason": None})
    else:
        ev = e.evidence[0]
        block = await repo.get_block(ev.block_id) if keep_text else None
        doc_class = None
        if block is not None:
            doc_class = (await repo.document_row(block["document_id"]))["doc_class"]
        block_text = block["text"] if block else None
        if action.action == "correct":
            norm, err = normalize(action.value, spec)
            if err:
                raise ValidationFailed(err)
            if norm == e.normalized_value:
                updated = e.model_copy(update={"status": ExtractionStatus.APPROVED, "reason": None})
            else:
                manual = Evidence(
                    block_id=ev.block_id,
                    document_id=ev.document_id,
                    bbox=ev.bbox,
                    locator=ev.locator or None,
                    quote=f"corrected by {reviewer}: {action.value}",
                )
                updated = e.model_copy(
                    update={
                        "value": typed(norm, spec),
                        "normalized_value": norm,
                        "confidence": 1.0,
                        "extractor": "human",
                        "status": ExtractionStatus.APPROVED,
                        "reason": "corrected by reviewer",
                        "evidence": [*e.evidence, manual],
                        "alternatives": [*e.alternatives, _as_alternative(e)],
                        "signals": {**e.signals, "human": 1.0},
                    }
                )
                case = await record_correction(
                    repo,
                    schema.id,
                    spec,
                    e.field_path,
                    e,
                    updated.value,
                    "review:correct",
                    job_id,
                    block_text,
                    doc_class,
                )
        else:
            i = action.alternative_index or 0
            if i >= len(e.alternatives):
                raise ValidationFailed("alternative_index is out of range")
            alt = e.alternatives[i]
            if alt.normalized_value is None:
                raise Conflict("that alternative failed validation; correct the value instead")
            rest = [a for j, a in enumerate(e.alternatives) if j != i]
            updated = e.model_copy(
                update={
                    "value": alt.value,
                    "normalized_value": alt.normalized_value,
                    "confidence": max(alt.confidence, e.confidence),
                    "extractor": alt.extractor,
                    "evidence": alt.evidence,
                    "alternatives": [*rest, _as_alternative(e)],
                    "status": ExtractionStatus.APPROVED,
                    "reason": "alternative chosen by reviewer",
                }
            )
            case = await record_correction(
                repo,
                schema.id,
                spec,
                e.field_path,
                e,
                alt.value,
                "review:pick_alternative",
                job_id,
                block_text,
                doc_class,
            )
    await repo.update_extraction(job_id, updated, reviewed_by=reviewer)
    return field_item(updated), case
