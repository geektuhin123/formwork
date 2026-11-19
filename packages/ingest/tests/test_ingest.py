import json
import time

import pytest
from formwork_core.errors import FileTooLarge, UnsupportedFile
from formwork_core.models import BlockType
from formwork_ingest import StoredFile, detect_mime, ingest_file, validate_upload
from formwork_ingest.cli import main as cli_main
from formwork_ingest.order import reading_order
from formwork_ingest.parsers.sheet import find_islands
from formwork_testkit import synth


async def parse(ctx, name, data):
    return await ingest_file(StoredFile(name, data), ctx)


async def test_every_fixture_parses_and_every_block_is_locatable(ctx, fixtures):
    for name, data in fixtures.items():
        docs = await parse(ctx, name, data)
        assert docs and all(d.document.blocks for d in docs), name
        for d in docs:
            orders = [b.reading_order for b in d.document.blocks]
            assert orders == list(range(len(orders)))
            for b in d.document.blocks:
                assert b.bbox is not None or b.locator, (name, b.id)
                assert b.embedding is not None and len(b.embedding) == 1024


async def test_digital_pdf_blocks_tables_and_page_images(ctx, fixtures):
    (doc,) = await parse(ctx, "digital_1.pdf", fixtures["digital_1.pdf"])
    d = doc.document
    assert d.page_count == 1 and doc.meta["digital"] is True
    table = next(b for b in d.blocks if b.type is BlockType.TABLE)
    assert table.cells[0] == ["Description", "HSN", "Qty", "Rate", "Amount"] and len(table.cells) >= 3
    assert "M8 Hex Bolt Zinc Plated" in table.text or any("Bolt" in c or " " in c for c in table.cells[1])
    assert any(b.type is BlockType.TITLE and b.text == "TAX INVOICE" for b in d.blocks)
    text = "\n".join(b.text for b in d.blocks)
    assert "Invoice No: INV-2026-1000" in text and "Grand Total" in text
    assert all(b.ocr_confidence is None for b in d.blocks)
    assert await ctx.store.exists(f"{ctx.tenant_id}/documents/{d.id}/pages/1.png")
    # totals come after the table and before the footer
    order = {b.text.split(":")[0]: b.reading_order for b in d.blocks}
    assert (
        table.reading_order
        < order["Sub Total"]
        < order["Goods once sold will not be taken back. Subject to Pune jurisdiction."]
    )


async def test_two_column_pdf_reads_left_column_first(ctx, fixtures):
    (doc,) = await parse(ctx, "two_column.pdf", fixtures["two_column.pdf"])
    firsts = [b.text.split()[0] for b in doc.document.blocks]
    assert firsts == ["Vendor", "ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT"]


async def test_scanned_pdf_uses_ocr_keeps_cells_and_is_fast(ctx, fixtures):
    for name in ("scanned_1.pdf", "scanned_2.pdf"):
        started = time.monotonic()
        (doc,) = await parse(ctx, name, fixtures[name])
        elapsed = time.monotonic() - started
        assert elapsed < 6.0, f"{name} took {elapsed:.1f}s"
        d = doc.document
        assert doc.meta["digital"] is False
        assert all(b.ocr_confidence is not None and b.bbox is not None for b in d.blocks)
        table = next(b for b in d.blocks if b.type is BlockType.TABLE)
        assert table.cells and len(table.cells[0]) >= 3
    text = "\n".join(b.text for b in d.blocks)
    assert "PO No: PO-2026-0203" in text and "Total Received Qty" in text


async def test_image_goes_through_the_scan_path(ctx):
    png = synth.page_png(synth.grn_pdf(synth.make_order(3)))
    (doc,) = await parse(ctx, "photo.png", png)
    assert doc.document.mime_type == "image/png" and doc.document.page_count == 1
    assert "GRN No: GRN-5003" in "\n".join(b.text for b in doc.document.blocks)


async def test_xlsx_two_islands_and_unmerged_cells(ctx, fixtures):
    (doc,) = await parse(ctx, "two_tables.xlsx", fixtures["two_tables.xlsx"])
    ranges = [b for b in doc.document.blocks if b.type is BlockType.SHEET_RANGE]
    assert [b.locator for b in ranges] == ["Sheet1!A1:C4", "Sheet1!E7:F9"]
    assert ranges[0].cells[1][0] == ranges[0].cells[2][0] == "West"  # merged A2:A3 repeated
    assert ranges[0].bbox is None


def test_find_islands_side_by_side():
    import numpy as np

    mask = np.zeros((6, 9), dtype=bool)
    mask[0:4, 0:3] = True
    mask[1:6, 5:9] = True
    assert sorted(find_islands(mask)) == [(0, 0, 4, 3), (1, 5, 6, 9)]


async def test_email_attachment_is_a_child_document(ctx, fixtures):
    docs = await parse(ctx, "supplier.eml", fixtures["supplier.eml"])
    assert len(docs) == 2
    mail, att = docs
    assert att.parent_document_id == mail.document.id and att.document.mime_type == "application/pdf"
    assert {b.locator for b in mail.document.blocks} >= {"email.headers", "email.body#p0"}
    assert mail.meta["document_date"] == "2026-08-29"
    assert any(b.type is BlockType.TABLE for b in att.document.blocks)
    assert all(b.document_id == att.document.id for b in att.document.blocks)


async def test_csv_and_docx(ctx):
    (csv_doc,) = await parse(ctx, "x.csv", b"PO No;Qty\nPO-1;10\nPO-2;20\n")
    assert csv_doc.document.blocks[0].locator == "csv!A1:B3" and csv_doc.document.blocks[0].cells[2] == [
        "PO-2",
        "20",
    ]
    (docx_doc,) = await parse(ctx, "p.docx", synth.policy_docx())
    kinds = [b.type for b in docx_doc.document.blocks]
    assert kinds == [BlockType.TITLE, BlockType.KEY_VALUE, BlockType.TEXT, BlockType.TABLE]
    assert docx_doc.document.blocks[3].locator == "docx.table[0]"


async def test_sha256_dedupe_gives_stable_ids(ctx, fixtures):
    (a,) = await parse(ctx, "a.pdf", fixtures["digital_1.pdf"])
    (b,) = await parse(ctx, "renamed.pdf", fixtures["digital_1.pdf"])
    assert a.document.id == b.document.id and [x.id for x in a.document.blocks] == [
        x.id for x in b.document.blocks
    ]
    assert a.document.sha256 == b.document.sha256


def test_mime_detection_trusts_content_not_the_name(fixtures):
    assert detect_mime("invoice.csv", fixtures["digital_1.pdf"]) == "application/pdf"
    assert detect_mime("a.xlsx", fixtures["two_tables.xlsx"]).endswith("spreadsheetml.sheet")
    with pytest.raises(UnsupportedFile):
        detect_mime("payload.exe", b"MZ\x90\x00\x03\x00\x00\x00")
    with pytest.raises(UnsupportedFile):
        validate_upload("x.pdf", b"", 10)
    with pytest.raises(FileTooLarge):
        validate_upload("x.pdf", b"%PDF-" + b"0" * 100, 10)
    with pytest.raises(UnsupportedFile):
        detect_mime("x.zip", b"PK\x03\x04garbage")


def test_reading_order_keeps_non_positional_blocks_in_parser_order():
    from formwork_ingest.base import RawBlock

    blocks = [RawBlock(BlockType.TEXT, str(i), locator=f"l{i}") for i in range(3)]
    assert [b.text for b in reading_order(blocks)] == ["0", "1", "2"]


def test_cli_writes_canonical_document(tmp_path, fixtures):
    src = tmp_path / "inv.pdf"
    src.write_bytes(fixtures["digital_1.pdf"])
    out = tmp_path / "doc.json"
    assert cli_main([str(src), "--out", str(out), "--artifacts", str(tmp_path / "art")]) == 0
    doc = json.loads(out.read_text())
    assert doc["mime_type"] == "application/pdf" and doc["blocks"] and "embedding" not in doc["blocks"][0]
    assert cli_main([str(tmp_path / "missing.pdf")]) == 1
