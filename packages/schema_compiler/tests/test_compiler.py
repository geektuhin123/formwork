import json

import pytest
from formwork_core.errors import SchemaCompileError
from formwork_core.groups import field_groups
from formwork_core.models import FieldType, OutputFormat
from formwork_core.provider import FakeProvider
from formwork_schema_compiler import (
    JsonSchemaSource,
    LLMSchemaCompiler,
    PromptSource,
    SampleFileSource,
    compile_json_schema,
    weak_descriptions,
)

INVOICE = {
    "title": "AP invoices",
    "type": "object",
    "required": ["invoice_number", "total_amount"],
    "x-entity-key": ["invoice_number"],
    "x-record-grain": "one row per invoice",
    "properties": {
        "invoice_number": {
            "type": "string",
            "description": "The supplier's invoice number, labelled 'Invoice No' or 'Bill No'",
            "pattern": "^[A-Z0-9/-]+$",
        },
        "invoice_date": {"type": "string", "format": "date", "description": "Date"},
        "status": {
            "enum": ["paid", "unpaid"],
            "description": "Payment status printed on the invoice header area",
        },
        "vendor": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Supplier legal name"}},
        },
        "total_amount": {
            "type": "number",
            "description": "Final amount payable including all taxes, labelled 'Grand Total'",
            "x-rules": ["approx(total_amount, subtotal + tax)"],
            "x-merge-policy": "require_agreement",
        },
        "line_items": {"type": "array", "items": {"$ref": "#/$defs/line"}},
    },
    "$defs": {
        "line": {
            "type": "object",
            "required": ["amount"],
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Item description text in the first column of the table",
                },
                "amount": {
                    "type": "number",
                    "description": "Line amount in the last column of the line items table",
                },
            },
        }
    },
}


def test_json_schema_maps_deterministically():
    s = compile_json_schema(INVOICE, "ten_1")
    by = {f.path: f for f in s.leaf_fields()}
    assert (
        s.name == "AP invoices"
        and s.entity_key == ["invoice_number"]
        and s.record_grain == "one row per invoice"
    )
    assert s.source_hint == "json_schema" and s.version == 0 and s.output_format is OutputFormat.JSON
    assert by["invoice_number"].format == "^[A-Z0-9/-]+$" and by["invoice_number"].required
    assert by["invoice_date"].type is FieldType.DATE and not by["invoice_date"].required
    assert by["status"].type is FieldType.ENUM and by["status"].enum_values == ["paid", "unpaid"]
    assert by["vendor.name"].type is FieldType.STRING
    assert (
        by["total_amount"].rules == ["approx(total_amount, subtotal + tax)"]
        and by["total_amount"].merge_policy == "require_agreement"
    )
    assert by["line_items[].amount"].required and not by["line_items[].description"].required
    groups = field_groups(s)
    assert [g.name for g in groups] == ["header", "line_items"]
    assert (
        groups[0].key(by["vendor.name"]) == "vendor__name"
        and groups[1].key(by["line_items[].amount"]) == "amount"
    )


def test_weak_descriptions_are_flagged_under_8_words():
    weak = weak_descriptions(compile_json_schema(INVOICE, "t"))
    assert [w.path for w in weak] == ["invoice_date", "vendor.name"]
    assert weak[0].words == 1


def test_json_schema_errors():
    with pytest.raises(SchemaCompileError):
        compile_json_schema({"type": "string"}, "t")
    with pytest.raises(SchemaCompileError):
        compile_json_schema({"type": "object", "properties": {"a": {"$ref": "http://evil.example/x"}}}, "t")
    with pytest.raises(SchemaCompileError):
        compile_json_schema({"type": "object", "properties": {}}, "t")


async def test_sample_csv_infers_types_and_makes_one_llm_call():
    fake = FakeProvider()
    csv = b'invoice_number,invoice_date,vendor_name,total_amount,quantity\nINV-001,12/11/2026,Acme Traders,"18,450.00",12\nINV-002,13/11/2026,Kaveri,990.50,3\n'
    s = await LLMSchemaCompiler(fake, "ten_1").compile(SampleFileSource(filename="target.csv", data=csv))
    by = {f.path: f for f in s.fields}
    assert len(fake.calls) == 1 and fake.calls[0].purpose == "schema:describe"
    assert by["invoice_date"].type is FieldType.DATE and by["total_amount"].type is FieldType.NUMBER
    assert by["quantity"].type is FieldType.INTEGER and by["vendor_name"].type is FieldType.STRING
    assert (
        s.entity_key == ["invoice_number"]
        and s.output_format is OutputFormat.CSV
        and s.source_hint == "sample"
    )
    assert all(len(f.description.split()) >= 8 for f in s.fields) and not weak_descriptions(s)


async def test_sample_json_with_nested_rows_and_bad_samples():
    sample = [{"po_number": "PO-1", "lines": [{"sku": "A1", "qty": 2}]}]
    s = await LLMSchemaCompiler(FakeProvider(), "t").compile(
        SampleFileSource(filename="t.json", data=json.dumps(sample).encode())
    )
    assert [f.path for f in s.leaf_fields()] == ["po_number", "lines[].sku", "lines[].qty"]
    with pytest.raises(SchemaCompileError):
        await LLMSchemaCompiler(FakeProvider(), "t").compile(SampleFileSource(filename="t.pdf", data=b"%PDF"))


async def test_prompt_source_uses_tool_output_and_rejects_invalid_drafts():
    c = LLMSchemaCompiler(FakeProvider(), "t")
    s = await c.compile(
        PromptSource(
            prompt="From each invoice I need: invoice number, invoice date, vendor name and total amount"
        )
    )
    assert [f.path for f in s.fields] == ["invoice_number", "invoice_date", "vendor_name", "total_amount"]
    assert (
        s.fields[1].type is FieldType.DATE
        and s.fields[3].type is FieldType.NUMBER
        and s.entity_key == ["invoice_number"]
    )
    bad = FakeProvider(
        script=[
            {
                "name": "x",
                "record_grain": "g",
                "entity_key": [],
                "fields": [{"path": "a", "type": "enum", "description": "d"}],
            }
        ]
    )
    with pytest.raises(SchemaCompileError):
        await LLMSchemaCompiler(bad, "t").compile(PromptSource(prompt="anything"))
    j = await c.compile(JsonSchemaSource(json_schema=INVOICE))
    assert j.tenant_id == "t"
