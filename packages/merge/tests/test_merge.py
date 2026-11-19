from formwork_core.models import Evidence, Extraction, ExtractionStatus
from formwork_merge import MergePolicy, SourceInfo, match_keys, merge
from formwork_schema_compiler import compile_json_schema
from formwork_testkit.schemas import invoice_json_schema, reconciliation_json_schema

RECON = compile_json_schema(reconciliation_json_schema(), "t")
INV = compile_json_schema(invoice_json_schema(), "t")


def ex(doc, path, value, key=None, conf=0.9, norm="auto", schema=RECON):
    from formwork_core.normalize import normalize

    n = normalize(value, schema.field(path))[0] if norm == "auto" else norm
    return Extraction(
        record_key=key or f"doc:{doc}",
        field_path=path,
        value=value,
        normalized_value=n,
        confidence=conf,
        evidence=[Evidence(block_id=f"b_{doc}_{path}", document_id=doc, locator="l", quote=str(value))],
        extractor="llm:cheap",
        signals={"model": conf, "grounding": 1.0},
    )


def by(result):
    return {(e.record_key, e.field_path): e for e in result.extractions}


def test_three_sources_merge_into_one_record_by_exact_key():
    cands = [
        ex("po", "po_number", "PO-2026-0200", key="po-2026-0200"),
        ex("po", "ordered_qty", 100, key="po-2026-0200"),
        ex("inv", "po_number", "PO-2026-0200"),
        ex("inv", "invoice_number", "INV-7000"),
        ex("grn", "po_number", "PO-2026-0200"),
        ex("grn", "received_qty", 100),
        ex("grn", "ordered_qty", 100),
    ]
    r = merge(RECON, cands)
    assert set(r.record_sources) == {"po-2026-0200"} and r.record_sources["po-2026-0200"] == [
        "po",
        "inv",
        "grn",
    ]
    m = by(r)
    po = m[("po-2026-0200", "po_number")]
    assert len(po.evidence) == 3 and po.signals["agreement"] == 1.0 and po.confidence > 0.9
    assert m[("po-2026-0200", "ordered_qty")].status is ExtractionStatus.EXTRACTED and len(m) == 4


def test_key_normalisation_and_conservative_fuzzy_matching():
    assert match_keys({"po-2026-0200": {"a", "b"}, "po-2026-o200": {"c"}}) == {
        "po-2026-o200": "po-2026-0200"
    }  # OCR O/0
    assert (
        match_keys({"po-2026-0200": {"a", "b"}, "po-2026-0201": {"c"}}) == {}
    )  # one digit apart is a different order
    assert match_keys({"po-2026-0200": {"a"}, "po-2026-o200": {"a"}}) == {}  # same document -> two records
    assert match_keys({"acme-traders-pvt-ltd-77": {"a", "b"}, "acme-traders-pvt-77": {"c"}}) == {
        "acme-traders-pvt-77": "acme-traders-pvt-ltd-77"
    }
    cands = [
        ex("a", "po_number", " po/2026/0200 "),
        ex("b", "po_number", "PO-2026-0200"),
        ex("c", "po_number", "PO-2026-O2OO"),
        ex("c", "grn_number", "GRN-1"),
    ]
    r = merge(RECON, cands)
    assert list(r.record_sources) == ["po-2026-0200"] and r.key_aliases == {"po-2026-o2oo": "po-2026-0200"}
    alt = by(r)[("po-2026-0200", "po_number")].alternatives
    assert [a.value for a in alt] == ["PO-2026-O2OO"]  # the misread key is kept as an alternative, not lost


def test_require_agreement_conflict_keeps_alternatives_and_spelling_variants_agree():
    cands = [
        ex("po", "po_number", "PO-1"),
        ex("po", "vendor_name", "Shree Balaji Traders Pvt Ltd"),
        ex("po", "ordered_qty", 100),
        ex("inv", "po_number", "PO-1"),
        ex("inv", "vendor_name", "SHREE BALAJI TRADERS PVT. LTD."),
        ex("grn", "po_number", "PO-1"),
        ex("grn", "ordered_qty", 90, conf=0.95),
    ]
    m = by(merge(RECON, cands))
    assert (
        m[("po-1", "vendor_name")].status is ExtractionStatus.EXTRACTED
        and not m[("po-1", "vendor_name")].alternatives
    )
    q = m[("po-1", "ordered_qty")]
    assert q.status is ExtractionStatus.CONFLICT and {q.value, q.alternatives[0].value} == {100.0, 90.0}
    assert (
        "disagree" in q.reason
        and q.alternatives[0].evidence[0].document_id in {"po", "grn"}
        and q.signals["agreement"] == 0.5
    )


def policy_schema(policy):
    s = RECON.model_copy(deep=True)
    for f in s.fields:
        if f.path in ("invoice_total", "grn_number"):
            f.merge_policy = policy
    return s


def test_every_merge_policy():
    cands = [
        ex("old", "po_number", "PO-1"),
        ex("old", "invoice_total", 100, conf=0.95),
        ex("old", "grn_number", "G1"),
        ex("new", "po_number", "PO-1"),
        ex("new", "invoice_total", 120, conf=0.8),
        ex("new", "grn_number", "G2"),
    ]
    sources = {
        "old": SourceInfo("old", document_date="2026-01-01", priority=5, order=0),
        "new": SourceInfo("new", document_date="2026-03-01", priority=1, order=1),
    }
    val = lambda p: by(merge(policy_schema(p), cands, sources))[("po-1", "invoice_total")]  # noqa: E731
    assert (
        val(MergePolicy.PREFER_HIGHEST_CONFIDENCE).value == 100
        and val("prefer_highest_confidence").alternatives[0].value == 120
    )
    assert val(MergePolicy.PREFER_LATEST_SOURCE).value == 120
    sources["old"].priority = 0
    assert val(MergePolicy.PREFER_SOURCE_PRIORITY).value == 100
    assert val(MergePolicy.REQUIRE_AGREEMENT).status is ExtractionStatus.CONFLICT
    summed = val(MergePolicy.SUM)
    assert summed.value == 220 and len(summed.evidence) == 2
    assert by(merge(policy_schema("concat_unique"), cands, sources))[("po-1", "grn_number")].value == "G1; G2"
    assert val("no_such_policy").value == 100  # unknown policy falls back to the default


def test_failed_candidates_lose_to_valid_ones_and_unkeyed_documents_are_reported():
    bad = ex("a", "invoice_total", "n/a", conf=0.4, norm=None)
    good = ex("b", "invoice_total", 50)
    r = merge(
        RECON,
        [
            ex("a", "po_number", "PO-9"),
            bad,
            ex("b", "po_number", "PO-9"),
            good,
            ex("z", "vendor_name", "Orphan Ltd"),
        ],
    )
    assert by(r)[("po-9", "invoice_total")].value == 50
    assert r.unkeyed_documents == ["z"] and ("unkeyed-z", "vendor_name") in by(r)


def test_arrays_align_by_index_or_keep_the_best_source_whole():
    def li(doc, i, amount, conf=0.9):
        return ex(doc, f"line_items[{i}].amount", amount, conf=conf, schema=INV)

    head = [ex("a", "invoice_number", "INV-1", schema=INV), ex("b", "invoice_number", "INV-1", schema=INV)]
    same = by(merge(INV, [*head, li("a", 0, 10), li("a", 1, 20), li("b", 0, 10), li("b", 1, 25, conf=0.5)]))
    assert (
        same[("inv-1", "line_items[1].amount")].value == 20
        and same[("inv-1", "line_items[1].amount")].alternatives[0].value == 25
    )
    ragged = by(merge(INV, [*head, li("a", 0, 10), li("a", 1, 20), li("b", 0, 11, conf=0.6)]))
    assert (
        ragged[("inv-1", "line_items[0].amount")].value == 10
        and not ragged[("inv-1", "line_items[0].amount")].alternatives
    )
