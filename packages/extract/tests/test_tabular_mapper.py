import time

import pytest
from formwork_core.context import JobContext
from formwork_core.errors import MappingPlanError
from formwork_core.models import BlockType, ExtractionStatus, FieldSpec, FieldType, TargetSchema
from formwork_core.provider import FakeProvider
from formwork_extract.tabular_mapper import (
    MappingPlan,
    MemoryPlanCache,
    TabularMapper,
    header_signature,
    validate_plan,
)
from formwork_ingest import StoredFile, ingest_file
from formwork_testkit import synth


def F(path, type_, desc):
    return FieldSpec(path=path, type=type_, description=desc)


SCHEMA = TargetSchema(
    id="sch_reg",
    tenant_id="t",
    name="Invoice register",
    version=1,
    record_grain="one row per invoice",
    entity_key=["invoice_number"],
    fields=[
        F(
            "invoice_number",
            FieldType.STRING,
            "The supplier's invoice number, labelled 'Invoice No' or 'Bill No'",
        ),
        F(
            "invoice_date",
            FieldType.DATE,
            "Date the invoice was issued, labelled 'Invoice Date' or 'Bill Dt'",
        ),
        F(
            "vendor_name",
            FieldType.STRING,
            "Legal name of the supplier, labelled 'Supplier Name' or 'Vendor'",
        ),
        F("vendor_gstin", FieldType.STRING, "Supplier GST identification number, labelled 'Supplier GSTIN'"),
        F(
            "po_number",
            FieldType.STRING,
            "Purchase order the invoice refers to, labelled 'PO Ref' or 'PO No'",
        ),
        F("subtotal", FieldType.NUMBER, "Amount before tax, labelled 'Taxable Amt' or 'Sub Total'"),
        F(
            "total_amount",
            FieldType.NUMBER,
            "Final amount payable including taxes, labelled 'Total Amt' or 'Grand Total'",
        ),
    ],
)
INVOICES = [synth.make_invoice(i) for i in range(6)]


async def sheet_block(data: bytes):
    (doc,) = await ingest_file(StoredFile("register.xlsx", data), JobContext.for_tests())
    return next(b for b in doc.document.blocks if b.type is BlockType.SHEET_RANGE)


async def test_messy_xlsx_maps_into_schema_with_row_locators():
    block = await sheet_block(synth.messy_invoice_xlsx(INVOICES))
    ctx = JobContext.for_tests()
    out = await TabularMapper().extract_block(SCHEMA, block, ctx)
    by = {(e.record_key, e.field_path): e for e in out}
    inv = INVOICES[0]
    key = "inv-2026-1000"
    assert by[(key, "total_amount")].value == inv.total_amount and by[
        (key, "subtotal")
    ].normalized_value == str(inv.subtotal).rstrip("0").rstrip(".")
    assert (
        by[(key, "invoice_date")].value == inv.invoice_date.isoformat()
    )  # both dd/mm/yyyy and dd-Mon-yyyy rows
    assert by[("inv-2026-1001", "invoice_date")].value == INVOICES[1].invoice_date.isoformat()
    assert by[(key, "vendor_name")].value == inv.vendor_name  # trimmed
    ev = by[(key, "total_amount")].evidence[0]
    assert ev.locator == "Register!A2:H2" and ev.quote.startswith("₹") and ev.bbox is None
    assert by[("inv-2026-1003", "po_number")].evidence[0].locator == "Register!A5:H5"
    assert all(e.extractor == "tabular" and e.evidence for e in out)
    assert len(out) == 6 * 7


async def test_same_sheet_twice_makes_exactly_one_llm_call():
    block = await sheet_block(synth.messy_invoice_xlsx(INVOICES))
    fake = FakeProvider()
    ctx = JobContext.for_tests(provider=fake)
    mapper = TabularMapper(MemoryPlanCache())
    first = await mapper.extract_block(SCHEMA, block, ctx)
    second = await mapper.extract_block(SCHEMA, block, ctx)
    assert len(fake.calls) == 1 and fake.calls[0].purpose == "mapping_plan"
    assert [e.value for e in first] == [e.value for e in second]


@pytest.mark.slow
async def test_50k_rows_map_under_20_seconds_with_one_llm_call():
    header = [
        "Bill No",
        "Bill Dt",
        "Supplier Name",
        "Supplier GSTIN",
        "PO Ref",
        "Taxable Amt",
        "Total Amt (INR)",
        "Remarks",
    ]
    rows = []
    for i in range(50_000):
        inv = INVOICES[i % 6]
        rows.append(
            [
                f"{inv.invoice_number}-{i}",
                inv.invoice_date.strftime("%d/%m/%Y"),
                inv.vendor_name,
                inv.vendor_gstin,
                inv.po_number,
                f"Rs. {inv.subtotal:,.2f}",
                f"₹{inv.total_amount:,.2f}",
                "",
            ]
        )
    from formwork_core.models import Block

    block = Block(
        id="blk_big",
        document_id="doc_big",
        type=BlockType.SHEET_RANGE,
        text="",
        cells=[header, *rows],
        locator="Register!A1:H50001",
        reading_order=0,
    )
    fake = FakeProvider()
    started = time.monotonic()
    out = await TabularMapper().extract_block(SCHEMA, block, JobContext.for_tests(provider=fake))
    elapsed = time.monotonic() - started
    print(f"\n50k rows -> {len(out)} extractions in {elapsed:.1f}s")
    assert len(fake.calls) == 1 and len(out) == 50_000 * 7 and elapsed < 20
    assert out[-1].evidence[0].locator == "Register!A50001:H50001"


HEADER = [
    "Bill No",
    "Bill Dt",
    "Supplier Name",
    "Supplier GSTIN",
    "PO Ref",
    "Taxable Amt",
    "Total Amt (INR)",
    "Remarks",
]
ROWS = [
    [
        i.invoice_number,
        i.invoice_date.strftime("%d/%m/%Y"),
        i.vendor_name,
        i.vendor_gstin,
        i.po_number,
        f"Rs. {i.subtotal:,.2f}",
        f"₹{i.total_amount:,.2f}",
        "ok",
    ]
    for i in INVOICES
]
SEEDED_WRONG = [
    ("Supplier Name", "total_amount", "to_number"),  # text into a number field
    ("Bill Dt", "subtotal", "to_number"),  # date into a number field
    ("Supplier GSTIN", "invoice_date", "parse_date"),  # id into a date field
    ("Remarks", "total_amount", "to_number"),  # free text into a number field
    ("PO Ref", "vendor_name", "identity"),  # text into a text field: type validation cannot see this one
]


def test_wrong_mappings_caught_by_type_validation_in_at_least_4_of_5():
    caught = 0
    for src, path, transform in SEEDED_WRONG:
        plan = MappingPlan(
            header_signature="x", columns=[{"source_column": src, "field_path": path, "transform": transform}]
        )
        caught += bool(validate_plan(plan, SCHEMA, HEADER, ROWS))
    assert caught == 4


def test_structural_plan_errors():
    plan = MappingPlan(
        header_signature="x",
        columns=[
            {"source_column": "Nope", "field_path": "po_number"},
            {"source_column": "PO Ref", "field_path": "nope"},
            {"source_column": "PO Ref", "field_path": "po_number", "transform": "eval"},
            {"source_column": "PO Ref", "field_path": "po_number"},
            {"source_column": "Bill No", "field_path": "po_number"},
        ],
    )
    errs = validate_plan(plan, SCHEMA, HEADER, ROWS)
    assert set(errs) == {0, 1, 2, 4} and "already mapped" in errs[4]
    assert header_signature(["A ", "b"]) == header_signature(["a", "B"])


async def test_bad_plan_is_retried_with_feedback_then_bad_columns_dropped():
    from formwork_core.models import Block

    block = Block(
        id="b",
        document_id="d",
        type=BlockType.SHEET_RANGE,
        text="",
        cells=[HEADER, *ROWS],
        locator="S!A1:H7",
        reading_order=0,
    )
    wrong = {
        "columns": [
            {"source_column": "Supplier Name", "field_path": "total_amount", "transform": "to_number"},
            {"source_column": "Bill No", "field_path": "invoice_number", "transform": "identity"},
        ]
    }
    fake = FakeProvider(script=[wrong, wrong])
    mapper = TabularMapper()
    out = await mapper.extract_block(SCHEMA, block, JobContext.for_tests(provider=fake))
    assert len(fake.calls) == 2 and "<previous_error>" in fake.calls[1].prompt
    assert {e.field_path for e in out} == {"invoice_number"}  # the wrong mapping never produced a value
    plan = MappingPlan.model_validate(next(iter(mapper.cache.plans.values())))
    assert len(plan.rejected) == 1
    with pytest.raises(MappingPlanError):
        await TabularMapper().extract_block(
            SCHEMA,
            block,
            JobContext.for_tests(provider=FakeProvider(script=[{"columns": []}, {"columns": []}])),
        )


async def test_unparseable_cell_is_kept_as_failed_validation_with_evidence():
    from formwork_core.models import Block

    rows = [[*ROWS[0][:6], "TBD", "ok"], ROWS[1], ROWS[2], ROWS[3], ROWS[4], ROWS[5]]
    block = Block(
        id="b",
        document_id="d",
        type=BlockType.SHEET_RANGE,
        text="",
        cells=[HEADER, *rows],
        locator="S!A1:H7",
        reading_order=0,
    )
    out = await TabularMapper().extract_block(SCHEMA, block, JobContext.for_tests())
    bad = next(e for e in out if e.record_key == "inv-2026-1000" and e.field_path == "total_amount")
    assert (
        bad.status is ExtractionStatus.FAILED_VALIDATION
        and bad.value == "TBD"
        and bad.evidence[0].quote == "TBD"
    )
