from formwork_core.context import JobContext
from formwork_core.embedding import HashingEmbedder
from formwork_core.groups import field_groups
from formwork_core.models import Block, BlockType, CanonicalDocument
from formwork_core.provider import FakeProvider
from formwork_retrieval import BM25, classify_document, select_blocks
from formwork_schema_compiler import compile_json_schema
from formwork_testkit.schemas import invoice_json_schema

SCHEMA = compile_json_schema(invoice_json_schema(), "t")
HEADER, LINES = field_groups(SCHEMA)


def doc(texts, pages=1):
    blocks = [
        Block(id=f"b{i}", document_id="d", type=BlockType.TEXT, text=t, locator=f"l{i}", reading_order=i)
        for i, t in enumerate(texts)
    ]
    return CanonicalDocument(
        id="d",
        job_id="j",
        filename="f.pdf",
        mime_type="application/pdf",
        sha256="x",
        page_count=pages,
        blocks=blocks,
    )


def test_bm25_ranks_matching_text_first():
    scores = BM25(
        ["terms and conditions of carriage", "grand total amount payable 1,180.00", "lorem ipsum"]
    ).scores("total amount payable")
    assert scores.index(max(scores)) == 1 and scores[2] == 0


def test_small_documents_skip_retrieval_and_large_ones_keep_topk_with_neighbours():
    small = doc(["a", "b", "c"])
    assert select_blocks(small, HEADER, HashingEmbedder()) == small.blocks
    filler = [
        f"Clause {i}: the parties agree to the general terms of carriage and storage." for i in range(100)
    ]
    filler[40] = (
        "Invoice No: INV-9 Invoice Date: 01/02/2026 Supplier: Kaveri Industrial Supplies GSTIN: 29AAGCK4421M1ZP"
    )
    filler[77] = "Sub Total: 100.00 CGST: 9.00 SGST: 9.00 Grand Total: 118.00 Currency: INR"
    big = doc(filler, pages=9)
    picked = select_blocks(big, HEADER, HashingEmbedder(), top_k=4, neighbours=1)
    ids = [b.id for b in picked]
    assert {"b40", "b77", "b39", "b41", "b76", "b78"} <= set(ids) and len(ids) <= 12
    assert ids == sorted(ids, key=lambda x: int(x[1:]))  # reading order preserved
    assert len(select_blocks(big, HEADER, HashingEmbedder(), force_all=True)) == 100


async def test_classify_is_one_cheap_call_on_the_first_two_pages():
    fake = FakeProvider()
    cls = await classify_document(
        doc(["TAX INVOICE", "Invoice No: 1", "Purchase Order: PO-1"]), JobContext.for_tests(provider=fake)
    )
    assert (
        cls == "invoice"
        and len(fake.calls) == 1
        and fake.calls[0].tier == "cheap"
        and fake.calls[0].purpose == "classify"
    )
    assert (
        await classify_document(doc(["GOODS RECEIPT NOTE", "GRN No: 5"]), JobContext.for_tests())
        == "goods_receipt"
    )
    weird = FakeProvider(script=[{"doc_class": "ransom_note", "confidence": 1}])
    assert await classify_document(doc(["x"]), JobContext.for_tests(provider=weird)) == "other"
