"""Regenerate the quickstart sample files in samples/: uv run python scripts/make_samples.py

Two synthetic purchase orders (the same ones apps/web/e2e/test_flows.py drives) with a seeded short
shipment and a seeded quantity conflict, so Review and the Lineage graph both have something to show
on the first try. Everything is synthetic (formwork_testkit.synth); nothing here is real vendor data.
"""

from __future__ import annotations

import json
from pathlib import Path

from formwork_testkit import synth
from formwork_testkit.schemas import reconciliation_json_schema

ROOT = Path(__file__).parent.parent / "samples"


def build() -> None:
    ROOT.mkdir(exist_ok=True)
    orders = [synth.make_order(3), synth.make_order(4)]  # short shipment, quantity conflict
    (ROOT / "schema.json").write_text(json.dumps(reconciliation_json_schema(), indent=1) + "\n")
    (ROOT / "po_register.xlsx").write_bytes(synth.po_register_xlsx(orders))
    for o in orders:
        (ROOT / f"invoice_{o.po_number}.pdf").write_bytes(synth.invoice_pdf(o.invoice))
        (ROOT / f"grn_{o.po_number}_scan.pdf").write_bytes(synth.scan_pdf(synth.grn_pdf(o), seed=5))
    print(f"wrote {len(list(ROOT.iterdir()))} files to {ROOT}")


if __name__ == "__main__":
    build()
