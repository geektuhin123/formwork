"""Assemble extractions into records in schema field order."""

from __future__ import annotations

from typing import Any

from formwork_core.models import Extraction, ExtractionStatus, FieldSpec, FieldType, TargetSchema
from formwork_core.paths import array_index, spec_path


def assemble(
    schema: TargetSchema, extractions: list[Extraction], only_approved: bool = False
) -> dict[str, dict[str, Any]]:
    """-> {record_key: nested record}. Fields with no (eligible) value are None; arrays keep row order."""
    by_record: dict[str, dict[str, Extraction]] = {}
    for e in extractions:
        if only_approved and e.status is not ExtractionStatus.APPROVED:
            continue
        by_record.setdefault(e.record_key, {})[e.field_path] = e
    out: dict[str, dict[str, Any]] = {}
    for key in sorted(by_record):
        values = by_record[key]

        def build(
            fields: list[FieldSpec], index: int | None, values: dict[str, Extraction] = values
        ) -> dict[str, Any]:
            rec: dict[str, Any] = {}
            for f in fields:
                if f.type is FieldType.ARRAY:
                    n = (
                        max(
                            (array_index(p) or 0) + 1
                            for p in values
                            if spec_path(p).startswith(f.path + "[]")
                        )
                        if any(spec_path(p).startswith(f.path + "[]") for p in values)
                        else 0
                    )
                    rec[f.name] = [build(f.children, i) for i in range(n)]
                elif f.type is FieldType.OBJECT:
                    rec[f.name] = build(f.children, index)
                else:
                    path = f.path.replace("[]", f"[{index}]", 1) if index is not None else f.path
                    hit = values.get(path)
                    rec[f.name] = hit.value if hit is not None and hit.normalized_value is not None else None
            return rec

        out[key] = build(schema.fields, None)
    return out


def flatten(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested record -> {concrete field path: value}; the inverse view used by scorers and tabular writers."""
    out: dict[str, Any] = {}
    for k, v in record.items():
        path = f"{prefix}{k}"
        if isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.update(flatten(item, f"{path}[{i}]."))
        elif isinstance(v, dict):
            out.update(flatten(v, f"{path}."))
        else:
            out[path] = v
    return out
