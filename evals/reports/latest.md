# Eval report - 2026-11-16T20:31:56Z

Mode: **offline** (FakeProvider; validates the pipeline, not model quality)

| Dataset | F1 | Precision | Recall | Ungrounded | Auto-approve rate | Auto-approved precision | Cost / page | Corrections | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| invoices | 0.977 | 0.996 | 0.958 | 0 | 99.1% | 0.996 | $0.0171 | 0/0 | pass |
| po_vs_invoice | 1.000 | 1.000 | 1.000 | 0 | 93.3% | 1.000 | $0.0103 | 0/0 | pass |

## invoices

20 documents, 20 pages, 20 records, 78 LLM calls, 8.5 s, 5 fields in review, 0/0 seeded conflicts surfaced.

No metric changed since the previous run.

Worst fields: line_items[].amount (fp 0, fn 4), line_items[].description (fp 0, fn 4), line_items[].hsn_code (fp 0, fn 4), line_items[].quantity (fp 0, fn 4), line_items[].unit_price (fp 0, fn 4)

## po_vs_invoice

31 documents, 31 pages, 15 records, 84 LLM calls, 13.2 s, 11 fields in review, 9/9 seeded conflicts surfaced.

No metric changed since the previous run.
