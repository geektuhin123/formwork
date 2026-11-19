# Formwork build progress

Read this first in a fresh session. Source of truth for the plan: `docs/lld.md`. Rules: `CLAUDE.md`.

## How to resume
```bash
cd /home/claude/formwork-app
uv sync                                   # Python deps (workspace)
.venv/bin/ruff check . && .venv/bin/mypy packages apps && .venv/bin/pytest -q
```
Then open the first phase below that is not marked DONE and follow its section in `docs/lld.md`.
Commit after each phase: `feat(phase-N): ...`. No Co-Authored-By / Claude-Session trailers.

## Environment facts (this sandbox)
- Docker CLI is present but there is no daemon: `docker compose` cannot run here. `docker-compose.yml` is written but untested.
- DB tests start a throwaway Postgres 16 from local binaries (`packages/testkit/formwork_testkit/pg.py`), with pgvector
  (`apt-get install postgresql-16-pgvector`). In CI set `FORMWORK_TEST_DATABASE_ADMIN_URL` / `FORMWORK_TEST_DATABASE_URL`.
- `ANTHROPIC_API_KEY` is not set. All LLM paths run on `FakeProvider` + `HeuristicModel` (rule-based, reads the real prompt).
- Python 3.13 locally; `requires-python >=3.12`.

## Phase status
| Phase | Status |
| --- | --- |
| 0 Foundation | DONE |
| 1 Ingest | DONE |
| 2 Schema compiler + tabular mapper | todo |
| 3 Retrieval, router, extractors | todo |
| 4 Merge, validate, repair | todo |
| 5 API, worker, output | todo |
| 6 Review UI, feedback, evals | todo |

## Phase 0 - Foundation (DONE)
Built: uv workspace (13 packages + 2 apps), `formwork_core` (models, paths, ids, errors, config, provider, heuristic
model, prompting, embedding, storage, security, logging, telemetry, db tables/session/repo), Alembic migration `0001`
with RLS on 15 tables, docker-compose, `.claude/` hook + reviewer agent + commands, `.env.example`, ADR 0001/0002.

Acceptance:
- `pytest` green: 16 tests in packages/core.
- `Extraction` without evidence raises: `test_extraction_without_evidence_raises` (and a DB CHECK constraint backs it).
- One cassette-backed provider call writes an `llm_calls` row: `test_cassette_call_writes_llm_calls_row`.
- Migration up/down: `test_migration_down_and_up`. RLS isolation: `test_rls_isolates_tenants`.

Deviations: see `docs/adr/0001-core-model-additions.md`, `docs/adr/0002-environment-substitutions.md`.
The Anthropic cassette is hand-built in the Messages API shape (no key to record with).

## Phase 1 - Ingest (DONE)
Built: `packages/ingest` (6 parsers, OCR + geometric layout, reading order, sha256-stable ids, embeddings, page
images + crops to the object store, `ingest` CLI) and `packages/testkit/formwork_testkit/synth.py` (seeded synthetic
Indian AP documents: GST invoices in 3 label templates, PO register, GRN, supplier email, scans).

Acceptance (13 tests in packages/ingest):
- Every fixture parses (2 digital PDFs, 2 scanned PDFs, two-column PDF, two-table XLSX, EML with PDF attachment): pass.
- Every block has a bbox or a locator: pass (also enforced by the model and a DB CHECK).
- Two-column PDF reading order: pass (title, ALPHA, BRAVO, CHARLIE, DELTA, ECHO, FOXTROT).
- XLSX yields 2 `SHEET_RANGE` blocks (`Sheet1!A1:C4`, `Sheet1!E7:F9`), merged cells repeated: pass.
- Scanned page under 6 s on CPU: 0.84-0.89 s per page here (2 vCPU, Tesseract 5.3, 200 DPI).

Deviations:
- Layout/OCR is Tesseract + a geometric layout pass (rule-line removal, row/column clustering), not Docling +
  PaddleOCR (ADR 0002). Tables on scans are found from word geometry; borderless digital-PDF tables fall back to
  text blocks (PyMuPDF `find_tables` needs ruling lines).
- Fixtures are generated at test time by the testkit instead of being committed under tests/fixtures.
- `Parser.parse` returns a `ParsedDocument` wrapper (document + page images + meta + children) rather than a bare
  `CanonicalDocument`, so email attachments and page images travel with it.
Known gaps: no handwriting/checkbox detection (those block types are never produced yet); evidence boxes are
block-level, not word-level; `.xls`, `.msg`, `.pptx` unsupported; OCR language is English only.
