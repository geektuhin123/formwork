# Formwork

Schema-driven document transformation. You supply a target format and a pile of unstructured sources (PDFs, scans,
photos, spreadsheets, DOCX, email); Formwork returns one dataset in exactly that format, with every value cited to the
source file, page and region it came from. Low-confidence and conflicting fields go to a review queue; every reviewer
correction becomes an eval case.

Status: MVP phases 0-6 of `docs/lld.md` are built. Read `PROGRESS.md` for measured acceptance results, deviations and
what is left before a commercial launch. All model-dependent numbers so far are **offline** (no API key was available).

## Architecture

```
 client / console ──► API (FastAPI) ──► Postgres 16 + pgvector  ◄── worker (arq on Redis)
                         │                  jobs, schemas, blocks,        │
                         └──► object store  extractions, lineage,  ◄──────┘
                              (S3 / MinIO)  llm_calls, eval_cases
```

A job is a chain of idempotent stage tasks keyed by `(job, stage, document, field_group)`:

| Stage | Package | What it does |
| --- | --- | --- |
| ingest | `packages/ingest` | Any file -> `CanonicalDocument` blocks with a bbox or locator; OCR for scans; sha256 dedupe |
| classify + retrieve | `packages/retrieval` | Document class; BM25 + vector top-k blocks per field group |
| route | `packages/router` | Cheapest extractor per block: tabular plan, table, text, vision |
| extract | `packages/extract` | Tool-use extraction; every value needs a quote that is found in its block (grounding) |
| merge | `packages/merge` | One record set across sources; merge policies; losers kept as alternatives |
| validate + repair | `packages/validate` | Types, formats, `simpleeval` rules; re-extract failures (max 2, budget-aware) |
| output + feedback | `packages/output`, `packages/feedback` | JSON/CSV/XLSX + `lineage.json`; corrections -> eval cases |

`packages/core` holds the six contract models, `ModelProvider` (the only door to an LLM: cost logging, per-job response
cache, budgets), config, storage and the tenant-scoped repository. `packages/pipeline` orchestrates the stages for both
the worker and the eval runner. `apps/api`, `apps/worker` and `apps/web` are the three deployables.

Guarantees enforced in code and tests: no `Extraction` without evidence (model validator + DB CHECK); ungrounded
values never reach output; rules never touch `eval`; every query carries `tenant_id` and Postgres row-level security
backs it; document text is treated as untrusted (masking, evidence-overlap rejection, rule checks).

## Run it locally

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22, Tesseract (`tesseract-ocr`).

```bash
uv sync
cp .env.example .env                 # set FORMWORK_API_KEY_PEPPER; add ANTHROPIC_API_KEY to use real models
```

**With Docker** (Postgres + pgvector, Redis, MinIO, API, worker, console):

```bash
docker compose up -d --build
docker compose exec api formwork-admin create-tenant "Acme"     # prints the API key and webhook secret once
FORMWORK_API_KEY=fw_live_... docker compose up -d web           # console at http://localhost:3000
```

**Without Docker** (needs local `postgresql-16` + `postgresql-16-pgvector` binaries; throwaway data):

```bash
(cd apps/web && npm ci && npm run build)
uv run python scripts/dev_stack.py   # API :8000, console :3000, prints an API key
```

Without `ANTHROPIC_API_KEY` everything runs on the deterministic `FakeProvider` (a rule-based stand-in that reads the
same prompts). It is good for development and plumbing tests, not for judging extraction quality.

## API in five calls

```bash
API=http://localhost:8000; KEY=fw_live_...
# 1. draft a schema (also: {"source":"prompt","prompt":"..."} or POST /v1/schemas/compile/sample with a file)
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d @- $API/v1/schemas/compile <<< '{"source":"json_schema","json_schema":'"$(cat evals/datasets/invoices/schema.json)"'}' > draft.json
# 2. save it (version 1); weak field descriptions come back as warnings
jq '.schema | {name,record_grain,entity_key,fields,output_format}' draft.json | \
  curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -d @- $API/v1/schemas | jq -r .schema.id
# 3. upload sources
curl -s -H "Authorization: Bearer $KEY" -F file=@evals/datasets/invoices/inputs/invoice_00.pdf $API/v1/files
# 4. start a job
curl -s -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"schema_id":"sch_...","file_ids":["fil_..."],"options":{"budget_usd":2.5,"auto_approve_threshold":0.9,"webhook_url":"https://example.com/hook"}}' $API/v1/jobs
# 5. poll, then fetch the result and its lineage
curl -s -H "Authorization: Bearer $KEY" $API/v1/jobs/job_...
curl -s -H "Authorization: Bearer $KEY" "$API/v1/jobs/job_.../result?format=csv" -o out.csv
curl -s -H "Authorization: Bearer $KEY" $API/v1/jobs/job_.../lineage
```

Review: `GET /v1/jobs/{id}/review`, then `POST /v1/jobs/{id}/review/{item_id}` with
`{"action":"approve"}`, `{"action":"correct","value":...}` or `{"action":"pick_alternative","alternative_index":0}`.
Full spec: `docs/openapi.json` (regenerate with `uv run python scripts/export_openapi.py`); interactive docs at `/docs`
outside prod. Python client: `packages/sdk` (`from formwork_sdk import Formwork, verify_webhook`).

Webhooks fire on every status change with `X-Formwork-Signature: t=<unix>,v1=<hex>`, where `v1` is
HMAC-SHA256 of `"<t>.<raw body>"` using the tenant's webhook secret (`GET /v1/account`). Reject timestamps older than
five minutes.

Errors are always `{"error": {"code": "...", "message": "..."}}`.

## Development

```bash
uv run ruff check . && uv run mypy packages apps && uv run pytest -q     # the bar for every change
uv run pytest packages/merge                                             # one package
uv run ingest some.pdf --out doc.json                                    # parse one file to CanonicalDocument JSON
uv run python evals/run_eval.py --all --gate                             # evals: live with a key, offline without
uv run python evals/datasets/build.py                                    # regenerate the synthetic datasets
uv run pytest apps/web/e2e -s                                            # Playwright flows (build the web app first)
uv run alembic upgrade head                                              # migrations (FORMWORK_DATABASE_ADMIN_URL)
```

DB tests start a throwaway Postgres from local binaries, or use `FORMWORK_TEST_DATABASE_ADMIN_URL` /
`FORMWORK_TEST_DATABASE_URL` (CI). The application role must not own the tables: row-level security only applies to
non-owners, and the API refuses to start in prod if its role bypasses it.

## Configuration

Everything is environment-driven (`FORMWORK_*`, see `.env.example`). The ones that matter first:
`FORMWORK_DATABASE_URL` (app role) and `FORMWORK_DATABASE_ADMIN_URL` (owner, migrations/admin CLI only),
`FORMWORK_API_KEY_PEPPER`, `ANTHROPIC_API_KEY`, `FORMWORK_MODEL_CHEAP` / `FORMWORK_MODEL_STRONG` (model ids are never
written at call sites), `FORMWORK_PRICE_*` (USD per million tokens used for cost accounting; set them to your
contract prices), `FORMWORK_DEFAULT_BUDGET_USD`, `OTEL_EXPORTER_OTLP_ENDPOINT`.

The web console keeps the API key server-side (`FORMWORK_API_KEY` in the Next.js process). It has no user login of
its own: set `FORMWORK_CONSOLE_PASSWORD` for a basic gate in pilots and put it behind your SSO for anything real.

## Repository map

`docs/lld.md` design · `docs/adr/` decisions · `docs/prototype.html` UI prototype · `design-system/MASTER.md` UI tokens
and rules · `packages/*` pipeline modules · `apps/{api,worker,web}` · `evals/` datasets, runner, reports ·
`migrations/` Alembic · `scripts/` dev stack, OpenAPI export, Claude Code hook · `PROGRESS.md` build log.
