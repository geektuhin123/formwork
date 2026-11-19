import csv
import io
import json

import openpyxl
from formwork_core.models import Alternative, Evidence, Extraction, ExtractionStatus, OutputFormat
from formwork_output import assemble, build_lineage, flatten, render, tabular_rows
from formwork_schema_compiler import compile_json_schema
from formwork_testkit.schemas import invoice_json_schema

INV = compile_json_schema(invoice_json_schema(), "t")


def ex(path, value, status=ExtractionStatus.APPROVED, key="inv-1", **kw):
    ev = Evidence(block_id="b1", document_id="d1", locator="p1", quote=str(value))
    return Extraction(
        id=f"ext_{path}",
        record_key=key,
        field_path=path,
        value=value,
        normalized_value=str(value),
        confidence=0.97,
        evidence=[ev],
        extractor="llm:haiku",
        status=status,
        **kw,
    )


ALT = Alternative(
    value=18405.0,
    normalized_value="18405",
    confidence=0.41,
    extractor="vlm:sonnet",
    evidence=[Evidence(block_id="b9", document_id="d9", locator="p", quote="Amount 18405.00")],
)
ITEMS = [
    ex("invoice_number", "INV-1"),
    ex("total_amount", 18450.0, alternatives=[ALT]),
    ex("vendor_name", "=cmd|' /C calc'!A0"),
    ex("subtotal", 99.0, status=ExtractionStatus.NEEDS_REVIEW),
    ex("line_items[0].description", "Bolt"),
    ex("line_items[0].amount", 10.0),
    ex("line_items[1].description", "Nut"),
    ex("line_items[1].amount", 5.5),
]


def test_records_follow_schema_field_order_and_only_approved_by_default():
    rec = assemble(INV, ITEMS, only_approved=True)["inv-1"]
    assert (
        list(rec)[:3] == ["invoice_number", "invoice_date", "vendor_name"] and list(rec)[-1] == "line_items"
    )
    assert (
        rec["subtotal"] is None
        and rec["total_amount"] == 18450.0
        and rec["line_items"][1]
        == {"description": "Nut", "hsn_code": None, "quantity": None, "unit_price": None, "amount": 5.5}
    )
    assert assemble(INV, ITEMS)["inv-1"]["subtotal"] == 99.0
    assert flatten(rec)["line_items[1].amount"] == 5.5


def test_json_csv_xlsx_writers_and_formula_injection_guard():
    data = json.loads(render(INV, ITEMS, OutputFormat.JSON))
    assert data[0]["invoice_number"] == "INV-1" and len(data[0]["line_items"]) == 2
    rows = list(csv.reader(io.StringIO(render(INV, ITEMS, OutputFormat.CSV).decode("utf-8-sig"))))
    assert (
        rows[0][:2] == ["invoice_number", "invoice_date"]
        and rows[0][-1] == "line_items.amount"
        and len(rows) == 3
    )
    assert (
        rows[1][0] == rows[2][0] == "INV-1" and rows[2][-1] == "5.5"
    )  # one row per line item, header repeated
    assert rows[1][2].startswith("'=")  # a cell that looks like a formula is neutralised
    lineage = build_lineage(
        "job_1", INV, ITEMS, [{"record_key": "inv-1", "field_path": "invoice_date", "reason": "missing"}]
    )
    wb = openpyxl.load_workbook(io.BytesIO(render(INV, ITEMS, OutputFormat.XLSX, lineage=lineage)))
    assert (
        wb.sheetnames == ["Data", "Lineage"]
        and wb["Data"].max_row == 3
        and wb["Data"]["C2"].value.startswith("'=")
    )
    assert wb["Lineage"].max_row == len(ITEMS) + 1
    header, _ = tabular_rows(INV, {})
    assert "line_items.description" in header


def test_lineage_matches_the_documented_shape():
    lin = build_lineage("job_1", INV, ITEMS)
    f = lin["records"]["inv-1"]["total_amount"]
    assert (
        f["value"] == 18450.0
        and f["confidence"] == 0.97
        and f["status"] == "approved"
        and f["extractor"] == "llm:haiku"
    )
    assert f["evidence"][0] == {"block_id": "b1", "document_id": "d1", "locator": "p1", "quote": "18450.0"}
    assert (
        f["alternatives"][0]["value"] == 18405.0
        and f["alternatives"][0]["document_id"] == "d9"
        and f["alternatives"][0]["quote"] == "Amount 18405.00"
    )
