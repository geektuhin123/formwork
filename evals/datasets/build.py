"""Regenerate the eval datasets from seeds: uv run python evals/datasets/build.py

Everything is synthetic (formwork_testkit.synth). Outputs are committed so eval runs are reproducible.
"""

from __future__ import annotations

import json
from pathlib import Path

from formwork_testkit import synth
from formwork_testkit.schemas import invoice_json_schema, reconciliation_json_schema

ROOT = Path(__file__).parent


def build_invoices() -> None:
    out = ROOT / "invoices"
    (out / "inputs").mkdir(parents=True, exist_ok=True)
    expected = {}
    for i in range(20):
        inv = synth.make_invoice(i)
        pdf = synth.invoice_pdf(inv)
        scanned = i % 4 == 3
        name = f"invoice_{i:02d}{'_scan' if scanned else ''}.pdf"
        (out / "inputs" / name).write_bytes(synth.scan_pdf(pdf, seed=i) if scanned else pdf)
        expected[inv.invoice_number] = {"source": name, "record": inv.expected()}
    (out / "expected.json").write_text(json.dumps(expected, indent=1))
    (out / "schema.json").write_text(json.dumps(invoice_json_schema(), indent=1))


def build_po_vs_invoice() -> None:
    out = ROOT / "po_vs_invoice"
    (out / "inputs").mkdir(parents=True, exist_ok=True)
    orders = [synth.make_order(i) for i in range(15)]
    (out / "inputs" / "po_register.xlsx").write_bytes(synth.po_register_xlsx(orders))
    expected = {}
    for i, o in enumerate(orders):
        (out / "inputs" / f"invoice_{i:02d}.pdf").write_bytes(synth.invoice_pdf(o.invoice))
        (out / "inputs" / f"grn_{i:02d}_scan.pdf").write_bytes(synth.scan_pdf(synth.grn_pdf(o), seed=100 + i))
        record = {
            "po_number": o.po_number,
            "vendor_name": o.vendor_name,
            "po_date": o.po_date.isoformat(),
            "invoice_number": o.invoice.invoice_number,
            "invoice_date": o.invoice.invoice_date.isoformat(),
            "grn_number": o.grn_number,
            "ordered_qty": o.ordered_qty,
            "received_qty": o.received_qty,
            "po_value": o.po_value,
            "invoice_subtotal": o.invoice.subtotal,
            "invoice_total": o.invoice.total_amount,
        }
        # A seeded conflict must surface on these fields (status conflict / needs_review, or alternatives kept).
        flagged = {
            "amount_mismatch": ["invoice_subtotal"],
            "short_shipment": ["received_qty"],
            "vendor_spelling": [],
            "qty_conflict": ["ordered_qty"],
        }
        expected[o.po_number.lower()] = {
            "record": record,
            "seeded": o.seeded,
            "must_flag": sorted({f for s in o.seeded for f in flagged[s]}),
        }
    (out / "expected.json").write_text(json.dumps(expected, indent=1))
    (out / "schema.json").write_text(json.dumps(reconciliation_json_schema(), indent=1))


if __name__ == "__main__":
    build_invoices()
    build_po_vs_invoice()
    for d in ("invoices", "po_vs_invoice"):
        files = list((ROOT / d / "inputs").iterdir())
        print(f"{d}: {len(files)} files, {sum(f.stat().st_size for f in files) // 1024} KB")
