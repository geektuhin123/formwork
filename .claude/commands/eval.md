---
description: Run the eval suite and write the report
---
Run `uv run python evals/run_eval.py --all`. It runs live when ANTHROPIC_API_KEY is set and offline (FakeProvider) otherwise.
Read evals/reports/latest.md, summarise the per-dataset metrics and the diff against the previous run, and flag any
gate failure (F1, ungrounded values, auto-approved precision, cost per page).
