"""Replay an eval case: re-extract the corrected field from its stored source block and compare with the reviewer's value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from formwork_core.context import JobContext
from formwork_core.groups import FieldGroup
from formwork_core.models import Block, BlockType, FieldSpec
from formwork_core.normalize import normalize, values_agree


@dataclass
class EvalCase:
    id: str
    field_path: str
    expected: Any
    observed: Any
    context: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EvalCase:
        return cls(
            id=str(d["id"]),
            field_path=str(d["field_path"]),
            expected=d.get("expected"),
            observed=d.get("observed"),
            context=dict(d.get("context") or {}),
        )


async def replay_case(case: EvalCase, ctx: JobContext) -> dict[str, Any]:
    """-> {id, field, expected, got, passed, replayable}. Cases without stored block text (zero retention) are skipped."""
    from formwork_extract import (
        TextExtractor,
    )  # local import: feedback must not pull extract in for API-only use

    text = case.context.get("block_text")
    spec_dict = case.context.get("field_spec")
    if not text or not spec_dict:
        return {
            "id": case.id,
            "field": case.field_path,
            "expected": case.expected,
            "got": None,
            "passed": False,
            "replayable": False,
        }
    spec = FieldSpec.model_validate(spec_dict)
    leaf = spec.model_copy(update={"path": spec.name})
    block = Block(
        id="case_block",
        document_id="case_doc",
        type=BlockType.TEXT,
        text=str(text),
        locator="eval.case",
        reading_order=0,
    )
    got = await TextExtractor().extract(FieldGroup(name="header", fields=(leaf,)), [block], ctx)
    value = got[0].normalized_value if got else None
    want = normalize(case.expected, spec)[0]
    return {
        "id": case.id,
        "field": case.field_path,
        "expected": case.expected,
        "got": got[0].value if got else None,
        "passed": value is not None and want is not None and values_agree(want, value, spec),
        "replayable": True,
    }
