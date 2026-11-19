import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import run_eval

CASE = {
    "id": "evc_demo",
    "field_path": "received_qty",
    "expected": 220,
    "observed": 250,
    "context": {
        "block_text": "Total Received Qty: 220\nReceived in good condition.",
        "field_spec": {
            "path": "received_qty",
            "type": "number",
            "description": "Total quantity received at the gate, labelled 'Total Received Qty'",
        },
    },
}


@pytest.mark.slow
async def test_eval_report_gates_diffs_and_includes_corrections(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    first = await run_eval.run(["po_vs_invoice"], tmp_path)
    r = first["datasets"][0]
    assert first["mode"] == "offline" and r["gate_failures"] == [] and r["ungrounded"] == 0
    assert r["records"] == 15 and r["seeded_conflicts_surfaced"] == r["seeded_conflicts"] == 9
    assert r["corrections"]["cases"] == 0 and r["diff"] == {}
    assert "First run" in (tmp_path / "latest.md").read_text()
    # A reviewer correction arrives (as exported from eval_cases) -> it shows up in the next run, and the report diffs.
    second = await run_eval.run(["po_vs_invoice"], tmp_path, extra_cases=[CASE])
    r2 = second["datasets"][0]
    assert r2["corrections"] == {
        "cases": 1,
        "replayable": 1,
        "passed": 1,
        "results": r2["corrections"]["results"],
    }
    assert (
        r2["corrections"]["results"][0]["id"] == "evc_demo" and r2["corrections"]["results"][0]["got"] == 220
    )
    assert r2["diff"]["corrections_pass_rate"] == {"previous": None, "current": 1.0, "delta": None}
    md = (tmp_path / "latest.md").read_text()
    assert "Change since the previous run" in md and "evc_demo" in md and "offline" in md
    assert json.loads((tmp_path / "previous.json").read_text())["datasets"][0]["corrections"]["cases"] == 0


def test_gate_logic_and_unreplayable_cases():
    ok = {
        "f1": 0.95,
        "ungrounded": 0,
        "auto_approved_precision": 1.0,
        "cost_per_page": 0.01,
        "records": 2,
        "expected_records": 2,
        "seeded_conflicts": 0,
        "seeded_conflicts_surfaced": 0,
    }
    assert run_eval.gate_failures(ok) == []
    bad = {
        **ok,
        "f1": 0.5,
        "ungrounded": 1,
        "auto_approved_precision": 0.9,
        "cost_per_page": 1.0,
        "records": 1,
    }
    assert len(run_eval.gate_failures(bad)) == 5
    assert run_eval.diff({"f1": 0.9}, {"f1": 0.8})["f1"]["delta"] == 0.1


@pytest.mark.db
async def test_review_correction_in_db_reaches_the_eval_run(db, tenant):
    from formwork_core.context import JobContext
    from formwork_core.db import TenantRepo, tenant_tx
    from formwork_core.models import Evidence, Extraction, FieldSpec, FieldType
    from formwork_feedback import EvalCase, export_cases, record_correction, replay_case

    spec = FieldSpec(
        path="invoice_total",
        type=FieldType.NUMBER,
        description="Invoice amount payable including taxes, labelled 'Grand Total'",
    )
    before = Extraction(
        record_key="po-1",
        field_path="invoice_total",
        value=1000.0,
        normalized_value="1000",
        confidence=0.9,
        extractor="llm:cheap",
        evidence=[Evidence(block_id="blk_1", document_id="doc_1", locator="p", quote="Sub Total: 1,000.00")],
    )
    async with tenant_tx(db, tenant["id"]) as conn:
        repo = TenantRepo(conn, tenant["id"])
        await record_correction(
            repo,
            "sch_1",
            spec,
            "invoice_total",
            before,
            1180.0,
            "review:correct",
            "job_1",
            "Sub Total: 1,000.00\nGrand Total: 1,180.00",
            "invoice",
        )
        exported = await export_cases(repo)
    assert exported[0]["expected"] == 1180.0 and exported[0]["observed"] == 1000.0 and json.dumps(exported)
    result = await replay_case(EvalCase.from_dict(exported[0]), JobContext.for_tests())
    assert result["passed"] and result["got"] == 1180.0
