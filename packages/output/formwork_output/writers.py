"""Writers: approved records in schema field order as JSON, CSV or XLSX, plus the lineage sidecar."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from formwork_core.models import Extraction, FieldSpec, FieldType, OutputFormat, TargetSchema
from openpyxl import Workbook

from formwork_output.records import assemble

CONTENT_TYPES = {
    OutputFormat.JSON: "application/json",
    OutputFormat.CSV: "text/csv; charset=utf-8",
    OutputFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(v: Any) -> Any:
    """Spreadsheet formula injection: text from a document must never execute when the export is opened."""
    if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES):
        return "'" + v
    return v


def _leaves(fields: list[FieldSpec]) -> list[FieldSpec]:
    out: list[FieldSpec] = []
    for f in fields:
        out.extend(_leaves(f.children) if f.type is FieldType.OBJECT else [f])
    return out


def _get(record: dict[str, Any], spec: FieldSpec, base: str = "") -> Any:
    node: Any = record
    rel = spec.path[len(base) :] if base and spec.path.startswith(base) else spec.path
    for part in rel.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def tabular_rows(
    schema: TargetSchema, records: dict[str, dict[str, Any]]
) -> tuple[list[str], list[list[Any]]]:
    """Flatten for CSV/XLSX. With exactly one array the grain is one row per array item, header fields repeated;
    with several arrays each is written as JSON text in its own column."""
    top = _leaves(schema.fields)
    arrays = [f for f in top if f.type is FieldType.ARRAY]
    scalars = [f for f in top if f.type is not FieldType.ARRAY]
    header = [f.path for f in scalars]
    rows: list[list[Any]] = []
    if len(arrays) == 1:
        arr = arrays[0]
        kids = _leaves(arr.children)
        header += [k.path.replace("[]", "") for k in kids]
        for rec in records.values():
            head = [_get(rec, f) for f in scalars]
            items = rec.get(arr.name) or [{}]
            for item in items:
                rows.append(head + [_get(item, k, arr.path + "[].") for k in kids])
    else:
        header += [a.path for a in arrays]
        for rec in records.values():
            rows.append(
                [_get(rec, f) for f in scalars]
                + [json.dumps(rec.get(a.name) or [], ensure_ascii=False) for a in arrays]
            )
    return header, rows


def write_json(schema: TargetSchema, records: dict[str, dict[str, Any]]) -> bytes:
    return json.dumps(list(records.values()), indent=2, ensure_ascii=False).encode()


def write_csv(schema: TargetSchema, records: dict[str, dict[str, Any]]) -> bytes:
    header, rows = tabular_rows(schema, records)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else _safe_cell(v) for v in r])
    return ("﻿" + buf.getvalue()).encode()


def write_xlsx(
    schema: TargetSchema, records: dict[str, dict[str, Any]], lineage: dict[str, Any] | None = None
) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Data"
    header, rows = tabular_rows(schema, records)
    ws.append(header)
    for r in rows:
        ws.append([_safe_cell(v) for v in r])
    if lineage:
        ls = wb.create_sheet("Lineage")
        ls.append(
            [
                "record",
                "field",
                "value",
                "confidence",
                "status",
                "extractor",
                "source_document",
                "location",
                "quote",
            ]
        )
        for key, fields in lineage.get("records", {}).items():
            for path, item in fields.items():
                ev = (item.get("evidence") or [{}])[0]
                bbox = ev.get("bbox")
                where = ev.get("locator") or (f"page {bbox['page']}" if bbox else "")
                ls.append(
                    [
                        key,
                        path,
                        _safe_cell(item.get("value")),
                        item.get("confidence"),
                        item.get("status"),
                        item.get("extractor"),
                        ev.get("document_id"),
                        where,
                        _safe_cell(ev.get("quote")),
                    ]
                )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def lineage_item(e: Extraction) -> dict[str, Any]:
    return {
        "id": e.id,
        "record_key": e.record_key,
        "field": e.field_path,
        "value": e.value,
        "normalized_value": e.normalized_value,
        "confidence": round(e.confidence, 4),
        "status": e.status.value,
        "extractor": e.extractor,
        "attempt": e.attempt,
        "reason": e.reason,
        "signals": e.signals,
        "evidence": [ev.model_dump(exclude_none=True) for ev in e.evidence],
        "alternatives": [
            {
                "value": a.value,
                "confidence": round(a.confidence, 4),
                "extractor": a.extractor,
                "document_id": a.evidence[0].document_id,
                "quote": a.evidence[0].quote,
                "evidence": [ev.model_dump(exclude_none=True) for ev in a.evidence],
            }
            for a in e.alternatives
        ],
    }


def build_lineage(
    job_id: str, schema: TargetSchema, extractions: list[Extraction], gaps: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """lineage.json: keyed by record and field, holding evidence, confidence and extractor."""
    records: dict[str, dict[str, Any]] = {}
    for e in sorted(extractions, key=lambda x: (x.record_key, x.field_path)):
        records.setdefault(e.record_key, {})[e.field_path] = lineage_item(e)
    return {
        "job_id": job_id,
        "schema_id": schema.id,
        "schema_version": schema.version,
        "records": records,
        "gaps": gaps or [],
    }


def render(
    schema: TargetSchema,
    extractions: list[Extraction],
    fmt: OutputFormat,
    only_approved: bool = True,
    lineage: dict[str, Any] | None = None,
) -> bytes:
    records = assemble(schema, extractions, only_approved=only_approved)
    if fmt is OutputFormat.CSV:
        return write_csv(schema, records)
    if fmt is OutputFormat.XLSX:
        return write_xlsx(schema, records, lineage)
    return write_json(schema, records)
