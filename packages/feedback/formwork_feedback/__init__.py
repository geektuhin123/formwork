"""Feedback: every reviewer correction becomes an eval case (source block, field spec, wrong value, right value).

The cases feed the regression suite (evals/run_eval.py re-extracts each one) and, later, per-class few-shot examples.
They are tenant-scoped rows: one tenant's corrections can never reach another tenant's prompts or evals.
"""

from __future__ import annotations

from typing import Any

from formwork_core.db.repo import TenantRepo
from formwork_core.models import Extraction, FieldSpec, Scalar

from formwork_feedback.replay import EvalCase, replay_case

__all__ = ["EvalCase", "case_context", "export_cases", "record_correction", "replay_case"]


def case_context(
    spec: FieldSpec, before: Extraction | None, block_text: str | None, job_id: str, doc_class: str | None
) -> dict[str, Any]:
    ev = before.evidence[0] if before and before.evidence else None
    return {
        "job_id": job_id,
        "doc_class": doc_class,
        "field_spec": spec.model_dump(mode="json"),
        "document_id": ev.document_id if ev else None,
        "quote": ev.quote if ev else None,
        "extractor": before.extractor if before else None,
        "block_text": block_text,
    }


async def record_correction(
    repo: TenantRepo,
    schema_id: str,
    spec: FieldSpec,
    field_path: str,
    before: Extraction | None,
    expected: Scalar,
    source: str,
    job_id: str,
    block_text: str | None = None,
    doc_class: str | None = None,
) -> str:
    block_id = before.evidence[0].block_id if before and before.evidence else ""
    return await repo.add_eval_case(
        schema_id=schema_id,
        field_path=field_path,
        block_id=block_id,
        expected=expected,
        observed=before.value if before else None,
        source=source,
        context=case_context(spec, before, block_text, job_id, doc_class),
    )


async def export_cases(repo: TenantRepo, schema_id: str | None = None) -> list[dict[str, Any]]:
    """Eval cases as plain dicts (JSON-serialisable) for evals/run_eval.py."""
    out = []
    for row in await repo.list_eval_cases(schema_id):
        out.append(
            {
                "id": row["id"],
                "schema_id": row["schema_id"],
                "field_path": row["field_path"],
                "block_id": row["block_id"],
                "expected": row["expected"],
                "observed": row["observed"],
                "source": row["source"],
                "context": row["context"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            }
        )
    return out
