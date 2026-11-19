import pytest
from formwork_core.models import (
    BBox,
    Block,
    BlockType,
    Evidence,
    Extraction,
    FieldSpec,
    FieldType,
    TargetSchema,
)
from formwork_core.paths import array_index, concrete_path, record_key, spec_path
from pydantic import ValidationError

EV = Evidence(block_id="b1", document_id="d1", locator="Sheet1!A2:F2", quote="INV-1")


def test_extraction_without_evidence_raises():
    with pytest.raises(ValidationError, match="evidence"):
        Extraction(
            record_key="k", field_path="total", value=1, confidence=0.9, evidence=[], extractor="llm:haiku"
        )


def test_extraction_with_evidence_ok_and_confidence_bounded():
    e = Extraction(
        record_key="k", field_path="total", value=1.5, confidence=0.9, evidence=[EV], extractor="tabular"
    )
    assert e.status == "extracted"
    with pytest.raises(ValidationError):
        Extraction(record_key="k", field_path="total", value=1, confidence=1.5, evidence=[EV], extractor="x")


def test_block_needs_bbox_or_locator():
    with pytest.raises(ValidationError, match="bbox or a locator"):
        Block(id="b", document_id="d", type=BlockType.TEXT, text="x", reading_order=0)
    Block(id="b", document_id="d", type=BlockType.TEXT, text="x", reading_order=0, locator="email.body")
    Block(
        id="b",
        document_id="d",
        type=BlockType.TEXT,
        text="x",
        reading_order=0,
        bbox=BBox(page=1, x0=0, y0=0, x1=1, y1=1),
    )


def test_evidence_needs_region_and_quote():
    with pytest.raises(ValidationError):
        Evidence(block_id="b", document_id="d", quote="x")
    with pytest.raises(ValidationError):
        Evidence(block_id="b", document_id="d", locator="l", quote="  ")


def test_bbox_normalised():
    with pytest.raises(ValidationError):
        BBox(page=1, x0=0, y0=0, x1=300, y1=1)


def test_schema_lookup_and_duplicates():
    li = FieldSpec(
        path="line_items",
        type=FieldType.ARRAY,
        description="lines",
        children=[FieldSpec(path="line_items[].amount", type=FieldType.NUMBER, description="line amount")],
    )
    s = TargetSchema(
        id="s",
        tenant_id="t",
        name="n",
        version=1,
        record_grain="one row per invoice",
        entity_key=["invoice_number"],
        fields=[FieldSpec(path="invoice_number", type=FieldType.STRING, description="number"), li],
    )
    assert s.field("line_items[3].amount") is not None
    assert [f.path for f in s.leaf_fields()] == ["invoice_number", "line_items[].amount"]
    with pytest.raises(ValidationError):
        FieldSpec(path="x", type=FieldType.ENUM, description="d")
    with pytest.raises(ValidationError, match="duplicate"):
        TargetSchema(
            id="s",
            tenant_id="t",
            name="n",
            version=1,
            record_grain="g",
            entity_key=[],
            fields=[FieldSpec(path="a", type=FieldType.STRING, description="d")] * 2,
        )


def test_paths():
    assert spec_path("line_items[12].amount") == "line_items[].amount"
    assert concrete_path("line_items[].amount", 3) == "line_items[3].amount"
    assert array_index("a[4].b") == 4 and array_index("a") is None
    assert record_key([" INV/2026-0042 "]) == "inv-2026-0042"
    assert record_key(["x" * 200]).startswith("h_")
