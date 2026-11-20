# Try it yourself

Two synthetic purchase orders — a `po_register.xlsx`, an invoice PDF and a scanned goods-receipt note (GRN) PDF
each — built so there's something to review: order `PO-2026-0203` has a short shipment (received less than
ordered), order `PO-2026-0204` has a quantity conflict (the GRN disagrees with the PO on how much was ordered).
Regenerate with `uv run python scripts/make_samples.py` from the repo root.

## Walkthrough

1. Start the stack: `uv run python scripts/dev_stack.py` (no `ANTHROPIC_API_KEY` needed — it runs on the offline
   `FakeProvider`), or `docker compose up -d --build`. The console prints its URL and an API key.
2. Open the console → **New job** → **JSON Schema** → paste this folder's `schema.json` → **Compile to fields**
   → **Continue to sources**.
3. Drop in all five files from this folder (`po_register.xlsx`, both `invoice_*.pdf`, both `grn_*_scan.pdf`) →
   **Run job**.
4. On **Review**: the two orders merge from their three source documents into two records. One field on each
   record needs you — open it, read the cited region on the source document, and **Approve** or **Edit value**.
5. Click **Lineage** next to **Grid** (it appears whenever a job has more than one source document) to see the
   same merge as a graph: both source documents converging on the record, fanning out to every field. Click a
   field to trace which document it was read from; click a source document to see everything it contributed.
6. **Export** once everything is approved, and compare the output against this folder's expectations in
   `evals/datasets/po_vs_invoice/expected.json` (same two orders, by `po_number`).
