import pytest
from formwork_core.errors import BudgetExceeded
from formwork_core.models import Evidence, Extraction, ExtractionStatus
from formwork_core.normalize import normalize
from formwork_schema_compiler import compile_json_schema
from formwork_testkit.schemas import invoice_json_schema
from formwork_validate import (
    Gap,
    RuleNotEvaluable,
    ValidateOptions,
    evaluate,
    find_gaps,
    ocr_confusion_fix,
    run_checks,
    validate_and_repair,
)

INV = compile_json_schema(invoice_json_schema(), "t")


def ex(path, value, conf=0.95, key="inv-1", norm="auto"):
    n = normalize(value, INV.field(path))[0] if norm == "auto" else norm
    return Extraction(
        record_key=key,
        field_path=path,
        value=value,
        normalized_value=n,
        confidence=conf,
        extractor="llm:cheap",
        evidence=[Evidence(block_id="b", document_id="d", locator="l", quote=str(value))],
        signals={"model": conf, "grounding": 1.0},
    )


def good(total=1180.0, line_amount=1000.0):
    return [
        ex("invoice_number", "INV-1"),
        ex("invoice_date", "2026-10-22"),
        ex("vendor_name", "Kaveri"),
        ex("subtotal", 1000.0),
        ex("cgst", 90.0),
        ex("sgst", 90.0),
        ex("total_amount", total),
        ex("line_items[0].description", "Bolt"),
        ex("line_items[0].quantity", 10),
        ex("line_items[0].unit_price", 100.0),
        ex("line_items[0].amount", line_amount),
    ]


def by(items):
    return {e.field_path: e for e in items}


def test_rules_use_simpleeval_and_never_python_eval():
    assert evaluate("approx(total, a + b, 0.05)", {"total": 3.0, "a": 1.0, "b": 2.01}) and not evaluate(
        "a == b", {"a": 1, "b": 2}
    )
    assert evaluate("approx(s, total(rows, 'amount'))", {"s": 30, "rows": [{"amount": 10}, {"amount": 20}]})
    for attack in (
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "().__class__.__bases__",
        "a.__class__",
        "[x for x in range(10**9)]",
        "9**9**9**9",
        "eval('1')",
        "exec('1')",
        "lambda: 1",
        "x" * 600,
    ):
        with pytest.raises(RuleNotEvaluable):
            evaluate(attack, {"a": 1})
    with pytest.raises(RuleNotEvaluable, match="missing"):
        evaluate("a == b", {"a": 1, "b": None})


def test_rule_checks_per_record_and_per_row():
    ok = by(run_checks(INV, good()))
    assert (
        ok["total_amount"].signals["rules"] == 1.0 and ok["total_amount"].status is ExtractionStatus.EXTRACTED
    )
    assert ok["line_items[0].amount"].signals["rules"] == 1.0 and ok["subtotal"].signals["rules"] == 1.0
    bad = by(run_checks(INV, good(total=0.0, line_amount=999.0)))
    assert (
        bad["total_amount"].status is ExtractionStatus.FAILED_VALIDATION
        and "rule failed" in bad["total_amount"].reason
    )
    assert (
        bad["line_items[0].amount"].status is ExtractionStatus.FAILED_VALIDATION
    )  # row rule: amount == qty * unit_price
    assert (
        bad["subtotal"].status is ExtractionStatus.FAILED_VALIDATION
    )  # cross-row rule: lines sum to the subtotal
    assert bad["total_amount"].confidence < ok["total_amount"].confidence


def test_field_checks_format_and_ocr_confusable_fix():
    assert ocr_confusion_fix("O6AAFFH7310K1Z3", INV.field("vendor_gstin").format) == "06AAFFH7310K1Z3"
    assert ocr_confusion_fix("hello", INV.field("vendor_gstin").format) is None
    out = by(
        run_checks(
            INV,
            [ex("vendor_gstin", "O6AAFFH7310K1Z3", norm=None), ex("invoice_date", "32/13/2026", norm=None)],
        )
    )
    g = out["vendor_gstin"]
    assert g.value == "06AAFFH7310K1Z3" and g.confidence <= 0.85 and "ocr_corrected" in g.reason
    assert (
        out["invoice_date"].status is ExtractionStatus.FAILED_VALIDATION
        and "not a date" in out["invoice_date"].reason
    )


def test_required_gaps_per_record_and_row():
    gaps = find_gaps(INV, [ex("invoice_number", "INV-1"), ex("line_items[0].quantity", 2)])
    paths = {g.field_path for g in gaps}
    assert {
        "invoice_date",
        "vendor_name",
        "subtotal",
        "total_amount",
        "line_items[0].description",
        "line_items[0].amount",
    } == paths
    assert "line_items" in {g.field_path for g in find_gaps(INV, [ex("invoice_number", "INV-1")])}


class FakeRepairer:
    def __init__(self, fixes, fail_with=None):
        self.fixes, self.calls, self.fail_with = fixes, [], fail_with

    async def repair(self, targets, attempt):
        self.calls.append((attempt, sorted(t.field_path for t in targets)))
        if self.fail_with:
            raise self.fail_with
        return [self.fixes[t.field_path] for t in targets if t.field_path in self.fixes]


async def test_repair_loop_fixes_failed_and_missing_fields_and_keeps_the_old_value():
    items = [e for e in good(total=0.0) if e.field_path != "vendor_name"]
    rep = FakeRepairer(
        {"total_amount": ex("total_amount", 1180.0), "vendor_name": ex("vendor_name", "Kaveri")}
    )
    res = await validate_and_repair(INV, items, rep)
    out = by(res.extractions)
    assert out["total_amount"].value == 1180.0 and out["total_amount"].status is ExtractionStatus.APPROVED
    assert out["total_amount"].attempt == 2 and out["total_amount"].alternatives[0].value == 0.0
    assert out["vendor_name"].status is ExtractionStatus.APPROVED and res.gaps == []
    assert (
        rep.calls == [(1, ["total_amount", "vendor_name"])]
        and res.stats["repaired"] == 2
        and res.stats["in_review"] == 0
    )


async def test_repair_stops_after_two_attempts_then_needs_review():
    rep = FakeRepairer({})
    res = await validate_and_repair(INV, good(total=0.0), rep, ValidateOptions(max_repair_attempts=2))
    assert [a for a, _ in rep.calls] == [1, 2]
    t = by(res.extractions)["total_amount"]
    assert (
        t.status is ExtractionStatus.FAILED_VALIDATION
        and res.stats["in_review"] == 1
        and res.stats["auto_approved"] == 10
    )


async def test_budget_exhaustion_stops_repair_and_sends_fields_to_review():
    rep = FakeRepairer({}, fail_with=BudgetExceeded("over"))
    res = await validate_and_repair(INV, good(total=0.0), rep)
    assert res.stats["budget_exhausted"] and len(rep.calls) == 1
    assert by(res.extractions)["total_amount"].status is ExtractionStatus.FAILED_VALIDATION


async def test_thresholds_conflicts_and_rejected_gaps():
    items = good()
    items[2] = ex("vendor_name", "Kaveri", conf=0.8)
    items[4] = ex("cgst", 90.0).model_copy(update={"status": ExtractionStatus.CONFLICT})
    res = await validate_and_repair(
        INV,
        items,
        None,
        ValidateOptions(auto_approve_threshold=0.9),
        rejected=[Gap("inv-1", "po_number", "ungrounded"), Gap("inv-1", "cgst", "ungrounded")],
    )
    out = by(res.extractions)
    assert (
        out["vendor_name"].status is ExtractionStatus.NEEDS_REVIEW
        and "threshold" in out["vendor_name"].reason
    )
    assert out["cgst"].status is ExtractionStatus.CONFLICT
    assert [g.field_path for g in res.gaps] == [
        "po_number"
    ]  # cgst has a value, so its rejected twin is not a gap
    assert res.stats["conflicts"] == 1 and res.stats["gaps"] == 1
    lenient = await validate_and_repair(INV, items, None, ValidateOptions(auto_approve_threshold=0.5))
    assert by(lenient.extractions)["vendor_name"].status is ExtractionStatus.APPROVED
