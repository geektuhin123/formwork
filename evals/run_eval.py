"""Eval suite: field-level precision / recall / F1, grounding, auto-approve quality and cost, per dataset.

    uv run python evals/run_eval.py --dataset invoices
    uv run python evals/run_eval.py --all --gate

Runs LIVE against the configured Claude models when ANTHROPIC_API_KEY is set; otherwise OFFLINE against the
deterministic FakeProvider (rule-based HeuristicModel). Offline numbers validate the pipeline, not model quality, and
every report says which mode produced it. Reviewer corrections (eval cases) are replayed on every run, from
evals/cases/<dataset>.jsonl and, with --tenant, from the eval_cases table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from formwork_core.config import Settings  # noqa: E402
from formwork_core.context import JobContext  # noqa: E402
from formwork_core.logging import configure_logging  # noqa: E402
from formwork_core.models import ExtractionStatus, TargetSchema  # noqa: E402
from formwork_core.normalize import normalize, values_agree  # noqa: E402
from formwork_core.provider import FakeProvider, ModelProvider, ModelTier, build_provider  # noqa: E402
from formwork_core.storage import MemoryObjectStore  # noqa: E402
from formwork_extract import GROUNDING_MIN, quote_match  # noqa: E402
from formwork_feedback import EvalCase, replay_case  # noqa: E402
from formwork_ingest import StoredFile  # noqa: E402
from formwork_output import assemble, flatten  # noqa: E402
from formwork_pipeline.local import LocalResult, run_local  # noqa: E402
from formwork_pipeline.stages import RunOptions  # noqa: E402
from formwork_schema_compiler import compile_json_schema  # noqa: E402
from scoring import score_records  # noqa: E402

DATASETS = ("invoices", "po_vs_invoice")
REPORTS = ROOT / "reports"
# MVP targets from docs/lld.md ("Evaluation"); the gate uses the offline floor when no key is present.
GATES = {"f1": 0.90, "ungrounded": 0, "auto_approved_precision": 0.99, "cost_per_page": 0.03}
METRICS = (
    "f1",
    "precision",
    "recall",
    "auto_approve_rate",
    "auto_approved_precision",
    "cost_per_page",
    "ungrounded",
    "corrections_pass_rate",
)


def provider_and_mode(settings: Settings) -> tuple[ModelProvider, str]:
    if settings.live_models:
        return build_provider(settings), "live"
    return FakeProvider(settings=settings), "offline"


def count_ungrounded(result: LocalResult) -> int:
    """Grounding check re-run on the final output: every model-extracted value's quote must be in its cited block."""
    blocks = {b.id: b for d in result.documents for b in d.document.blocks}
    bad = 0
    for e in result.extractions:
        for ev in e.evidence:
            block = blocks.get(ev.block_id)
            if block is not None and block.cells and block.type.value == "sheet_range":
                ok = any(ev.quote == c.strip() for row in block.cells for c in row)
            else:
                ok = block is not None and quote_match(ev.quote, block.text) >= GROUNDING_MIN
            bad += 0 if ok else 1
    return bad


def auto_approved_precision(
    schema: TargetSchema, result: LocalResult, expected: dict[str, dict[str, Any]]
) -> tuple[float, int]:
    flat = {k: flatten(v) for k, v in expected.items()}
    right = total = 0
    for e in result.extractions:
        want = flat.get(e.record_key, {}).get(e.field_path)
        spec = schema.field(e.field_path)
        if e.status is not ExtractionStatus.APPROVED or want is None or spec is None:
            continue
        total += 1
        right += values_agree(normalize(want, spec)[0], e.normalized_value, spec)
    return (right / total if total else 1.0), total


def load_cases(dataset: str, extra: list[dict[str, Any]] | None = None) -> list[EvalCase]:
    cases = list(extra or [])
    path = ROOT / "cases" / f"{dataset}.jsonl"
    if path.exists():
        cases += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [EvalCase.from_dict(c) for c in cases]


async def db_cases(tenant_id: str) -> list[dict[str, Any]]:
    from formwork_core.db import TenantRepo, app_engine, tenant_tx
    from formwork_feedback import export_cases

    engine = app_engine(Settings())
    try:
        async with tenant_tx(engine, tenant_id) as conn:
            return await export_cases(TenantRepo(conn, tenant_id))
    finally:
        await engine.dispose()


async def run_dataset(
    dataset: str,
    settings: Settings | None = None,
    extra_cases: list[dict[str, Any]] | None = None,
    provider: ModelProvider | None = None,
) -> dict[str, Any]:
    settings = settings or Settings()
    d = ROOT / "datasets" / dataset
    schema = compile_json_schema(json.loads((d / "schema.json").read_text()), "eval")
    spec = json.loads((d / "expected.json").read_text())
    expected = {k.lower(): v["record"] for k, v in spec.items()}
    files = [StoredFile(p.name, p.read_bytes()) for p in sorted((d / "inputs").iterdir())]
    live_provider, mode = provider_and_mode(settings)
    if provider is not None:
        live_provider, mode = provider, "offline"
    ctx = JobContext(
        tenant_id="eval",
        job_id=f"eval-{dataset}",
        provider=live_provider,
        store=MemoryObjectStore(),
        settings=settings,
    )
    started = time.monotonic()
    result = await run_local(
        schema, files, ctx, RunOptions(auto_approve_threshold=settings.auto_approve_threshold)
    )
    elapsed = time.monotonic() - started
    score = score_records(schema, expected, assemble(schema, result.extractions))
    aap, aa_n = auto_approved_precision(schema, result, expected)
    total_fields = len(result.extractions) + len(result.gaps)
    case_ctx = JobContext(
        tenant_id="eval",
        job_id=f"eval-{dataset}-cases",
        provider=live_provider,
        store=MemoryObjectStore(),
        settings=settings,
    )
    replays = [await replay_case(c, case_ctx) for c in load_cases(dataset, extra_cases)]
    replayable = [r for r in replays if r["replayable"]]
    flagged = {(k.lower(), f) for k, v in spec.items() for f in v.get("must_flag", [])}
    by = {(e.record_key, e.field_path): e for e in result.extractions}
    surfaced = sum(
        1
        for k in flagged
        if k in by and (by[k].status is not ExtractionStatus.APPROVED or by[k].alternatives)
    )
    return {
        "dataset": dataset,
        "mode": mode,
        "models": {
            "cheap": live_provider.model_for(ModelTier.CHEAP),
            "strong": live_provider.model_for(ModelTier.STRONG),
        },
        "documents": len(files),
        "pages": result.pages,
        "records": result.stats.get("records"),
        "expected_records": len(expected),
        "f1": round(score.f1, 4),
        "precision": round(score.precision, 4),
        "recall": round(score.recall, 4),
        "ungrounded": count_ungrounded(result),
        "rejected_by_grounding": len(result.rejected),
        "auto_approve_rate": round(aa_n / total_fields, 4) if total_fields else 0.0,
        "auto_approved_precision": round(aap, 4),
        "in_review": result.stats.get("in_review"),
        "seeded_conflicts": len(flagged),
        "seeded_conflicts_surfaced": surfaced,
        "cost_usd": round(result.cost_usd, 4),
        "cost_per_page": round(result.cost_usd / max(1, result.pages), 5),
        "llm_calls": len(result.calls),
        "seconds": round(elapsed, 1),
        "corrections": {
            "cases": len(replays),
            "replayable": len(replayable),
            "passed": sum(r["passed"] for r in replayable),
            "results": replays,
        },
        "corrections_pass_rate": round(sum(r["passed"] for r in replayable) / len(replayable), 4)
        if replayable
        else None,
        "worst_fields": sorted(
            ({"field": f, "fp": v[1], "fn": v[2]} for f, v in score.per_field.items() if v[1] + v[2]),
            key=lambda x: -(x["fp"] + x["fn"]),
        )[:5],
        "errors": score.errors[:25],
    }


def gate_failures(r: dict[str, Any]) -> list[str]:
    out = []
    if r["f1"] < GATES["f1"]:
        out.append(f"F1 {r['f1']} < {GATES['f1']}")
    if r["ungrounded"] > GATES["ungrounded"]:
        out.append(f"{r['ungrounded']} ungrounded value(s) in output")
    if r["auto_approved_precision"] < GATES["auto_approved_precision"]:
        out.append(
            f"auto-approved precision {r['auto_approved_precision']} < {GATES['auto_approved_precision']}"
        )
    if r["cost_per_page"] > GATES["cost_per_page"]:
        out.append(f"cost per page ${r['cost_per_page']} > ${GATES['cost_per_page']}")
    if r["records"] != r["expected_records"]:
        out.append(f"{r['records']} records, expected {r['expected_records']}")
    if r["seeded_conflicts_surfaced"] < r["seeded_conflicts"]:
        out.append(
            f"only {r['seeded_conflicts_surfaced']} of {r['seeded_conflicts']} seeded conflicts surfaced"
        )
    return out


def diff(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {}
    return {
        m: {
            "previous": previous.get(m),
            "current": current.get(m),
            "delta": round(current[m] - previous[m], 5)
            if isinstance(current.get(m), int | float) and isinstance(previous.get(m), int | float)
            else None,
        }
        for m in METRICS
        if current.get(m) != previous.get(m)
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Eval report - {report['generated_at']}",
        "",
        f"Mode: **{report['mode']}**"
        + (
            " (FakeProvider; validates the pipeline, not model quality)"
            if report["mode"] == "offline"
            else ""
        ),
        "",
    ]
    lines += [
        "| Dataset | F1 | Precision | Recall | Ungrounded | Auto-approve rate | Auto-approved precision | Cost / page | Corrections | Gate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["datasets"]:
        c = r["corrections"]
        lines.append(
            f"| {r['dataset']} | {r['f1']:.3f} | {r['precision']:.3f} | {r['recall']:.3f} | {r['ungrounded']} | "
            f"{r['auto_approve_rate']:.1%} | {r['auto_approved_precision']:.3f} | ${r['cost_per_page']:.4f} | "
            f"{c['passed']}/{c['replayable']} | {'pass' if not r['gate_failures'] else 'FAIL'} |"
        )
    for r in report["datasets"]:
        lines += [
            "",
            f"## {r['dataset']}",
            "",
            f"{r['documents']} documents, {r['pages']} pages, {r['records']} records, {r['llm_calls']} LLM calls, {r['seconds']} s, "
            f"{r['in_review']} fields in review, {r['seeded_conflicts_surfaced']}/{r['seeded_conflicts']} seeded conflicts surfaced.",
        ]
        if r["gate_failures"]:
            lines += ["", "Gate failures: " + "; ".join(r["gate_failures"])]
        if r["diff"]:
            lines += [
                "",
                "Change since the previous run:",
                "",
                "| Metric | Previous | Current | Delta |",
                "| --- | --- | --- | --- |",
            ]
            lines += [
                f"| {m} | {v['previous']} | {v['current']} | {v['delta'] if v['delta'] is not None else ''} |"
                for m, v in r["diff"].items()
            ]
        else:
            lines += [
                "",
                "No metric changed since the previous run."
                if report.get("had_previous")
                else "First run: nothing to diff against.",
            ]
        if r["corrections"]["results"]:
            lines += [
                "",
                "Reviewer corrections replayed:",
                "",
                "| Case | Field | Expected | Got | Result |",
                "| --- | --- | --- | --- | --- |",
            ]
            lines += [
                f"| {x['id']} | {x['field']} | {x['expected']} | {x['got']} | "
                f"{'pass' if x['passed'] else ('not replayable' if not x['replayable'] else 'FAIL')} |"
                for x in r["corrections"]["results"]
            ]
        if r["worst_fields"]:
            lines += [
                "",
                "Worst fields: "
                + ", ".join(f"{w['field']} (fp {w['fp']}, fn {w['fn']})" for w in r["worst_fields"]),
            ]
    return "\n".join(lines) + "\n"


async def run(
    datasets: list[str],
    reports_dir: Path = REPORTS,
    extra_cases: list[dict[str, Any]] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    latest = reports_dir / "latest.json"
    previous = json.loads(latest.read_text()) if latest.exists() else None
    prev_by = {r["dataset"]: r for r in (previous or {}).get("datasets", [])}
    results = []
    for name in datasets:
        r = await run_dataset(name, settings, extra_cases)
        r["gate_failures"] = gate_failures(r)
        r["diff"] = diff(r, prev_by.get(name))
        results.append(r)
    kept = [r for n, r in prev_by.items() if n not in datasets]  # datasets not in this run carry over
    report = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": results[0]["mode"] if results else "offline",
        "had_previous": previous is not None,
        "datasets": results + kept,
    }
    if previous is not None:
        (reports_dir / "previous.json").write_text(json.dumps(previous, indent=1))
    latest.write_text(json.dumps(report, indent=1))
    (reports_dir / "latest.md").write_text(markdown(report))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", choices=DATASETS, action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any dataset misses a gate")
    ap.add_argument(
        "--tenant", help="also replay this tenant's reviewer corrections from the eval_cases table"
    )
    ap.add_argument("--reports", type=Path, default=REPORTS)
    args = ap.parse_args(argv)
    configure_logging(level="WARNING")
    names = list(DATASETS) if args.all or not args.dataset else args.dataset
    extra = asyncio.run(db_cases(args.tenant)) if args.tenant else None
    report = asyncio.run(run(names, args.reports, extra))
    print(markdown(report))
    failed = [f"{r['dataset']}: {f}" for r in report["datasets"] for f in r["gate_failures"]]
    if args.gate and failed:
        print("EVAL GATE FAILED\n" + "\n".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
