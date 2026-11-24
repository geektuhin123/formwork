# Formwork roadmap

The plan for building features and enhancements on the Formwork MVP with Claude Code, one item per session. The MVP (phases 0–6 in `docs/lld.md`) is complete. This file picks up where `PROGRESS.md` ends: everything still needed to launch commercially, then the enhancements that make the product better than the alternatives.

Status as of 19 Nov 2026: 96 tests passing, ruff and strict mypy clean, offline eval F1 0.977 (invoices) and 1.000 (PO vs invoice). Every quality and cost number so far comes from `FakeProvider`, not a real model.

---

## How to use this file with Claude Code

### One item per session

1. Start a fresh Claude Code session in the repo root.
2. Paste the session prompt below with the item ID filled in, or run `/roadmap <ID>` once you've added the slash command from the last section.
3. Review the plan Claude proposes in plan mode against the item's acceptance criteria, then let it build.
4. Before you merge, ask the `reviewer` subagent to check the diff against the item.
5. Change the item's status in this file in the same PR.

### Session prompt

```text
Read CLAUDE.md, PROGRESS.md and docs/ROADMAP.md. Work on roadmap item <ID> only.
Start in plan mode: list the files you will touch, the tests you will add, and how you will prove each acceptance criterion.
Stay inside the packages the item names; ask before touching others. Changes to packages/core/formwork_core/models.py need an ADR in docs/adr/.
When done: uv run ruff check . && uv run mypy packages apps && uv run pytest -q must pass, and uv run python evals/run_eval.py --all --gate must not regress.
Update the item's status in docs/ROADMAP.md, add a line to PROGRESS.md, and commit with a conventional message (no Co-Authored-By or Claude-Session trailers).
```

### Definition of done for every item

- Each acceptance criterion is shown by a test, an eval number or a screenshot, named in the PR description.
- The bar passes: `uv run ruff check . && uv run mypy packages apps && uv run pytest -q`.
- The eval gate doesn't regress: `uv run python evals/run_eval.py --all --gate`.
- UI items: Playwright flows pass at 1440 px and 390 px, with screenshots attached.
- Docs are updated where behaviour changed: README, `docs/openapi.json` (regenerate it with `uv run python scripts/export_openapi.py`) and the ADRs.

### Status values

`todo` · `doing` · `done` · `parked`. Size: **S** is under half a day of Claude Code work, **M** is about a day, **L** is several sessions, so split it before starting.

---

## Launch-critical path

These items must be done before the first paying customer, in this order. Everything else on the roadmap can follow launch.

1. **M0** housekeeping → 2. **M1** live-model quality → 3. **M2-1/M2-2** real documents → 4. **M3** deployable infrastructure → 5. **M4-1 to M4-6** security → 6. **M5-1 to M5-4** accounts, keys, metering and billing → 7. **M4-8/M4-9** legal → 8. **M9-1/M9-2** observability → design-partner beta.

**Suggested slots for the new features (M11–M16):**
- **With the beta:** M13-1 to M13-3 (quality modes and cost estimates) and M16-1/M16-2 (evidence pack, Excel lineage sheet). They're cheap to build and help sell the product.
- **After the beta:**
  - M11 transformation layer
  - M14 insights
  - M12 RAG ingestion (M12-1 to M12-4)
  - M15 Power BI and Fabric delivery, only after the employment check in M15-0
- **When enterprise prospects ask:** M13-5 and M13-6 (bring your own key, deployment in the customer's own cloud), and M12-5/M12-6 (questions over documents).

---

## M0 · Housekeeping (do first)

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M0-1 | Fix stale references | S | todo |
| M0-2 | Fresh-clone check | S | todo |
| M0-3 | Push CI green on GitHub | S | todo |
| M0-4 | Pin and install UX skills | S | todo |

**M0-1 Fix stale references.** `CLAUDE.md` mentions `scripts/dev_up.sh`, which doesn't exist; the real script is `scripts/dev_stack.py`. Sweep CLAUDE.md, README and PROGRESS.md for commands that don't run, and make each one either run or go.
*Accept:* every command in CLAUDE.md and README runs on a fresh clone, or is deleted.

**M0-2 Fresh-clone check.** Add `scripts/check_fresh_clone.sh`. It clones the repo into a temporary directory, runs `uv sync`, the test bar and `next build`, and reports how long each step took.
*Accept:* the script passes on macOS (your laptop) and in CI.

**M0-3 Push CI green on GitHub.** The workflows in `.github/workflows/` have never run on GitHub. Fix whatever breaks there, such as Postgres service containers, pgvector, Tesseract or browser caching, and add a status badge to the README.
*Accept:* `ci.yml` is green on `main`, and `nightly-eval.yml` runs on a manual trigger.

**M0-4 Pin and install UX skills.** Read the SKILL.md and scripts of each skill listed in the LLD's "UX skills" section. Install the ones you accept into `.claude/skills/`, pinned to a commit, and write ADR 0003 recording which you took and why. Add `npx impeccable detect apps/web` to CI if you adopt impeccable.
*Accept:* the ADR exists, the installed skills load in a session, and the CI step runs.

---

## M1 · Live-model quality

Needs `ANTHROPIC_API_KEY` set in your shell and as a GitHub Actions secret.

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M1-1 | First live eval run and honest baseline | S | todo |
| M1-2 | Real prices and cost accounting | S | todo |
| M1-3 | Record real cassettes | S | todo |
| M1-4 | Prompt and field-description tuning loop | M | todo |
| M1-5 | Prompt caching | S | todo |
| M1-6 | Confidence calibration on real labels | M | todo |
| M1-7 | Model routing review | S | todo |

**M1-1 First live eval run.** Run `evals/run_eval.py --all` live. Save the report as the new baseline (`evals/reports/live-baseline.md`) next to the offline one, and write down the gap per field. Fix crashes, not quality, in this item.
*Accept:* a live report exists, the per-field gap is recorded in PROGRESS.md, and no live-only exceptions remain.

**M1-2 Real prices and cost accounting.** Set `FORMWORK_PRICE_*` from the current Anthropic pricing page. Check that `llm_calls.cost_usd` matches the usage shown in the Anthropic Console for one eval run, to within 2%.
*Accept:* the reconciliation note is in the PR, and the budget tests still pass.

**M1-3 Record real cassettes.** Replace the hand-built Anthropic cassette with recorded ones: a text extraction, a table, a vision call, a repair call and the schema compiler. Scrub them of keys and document text beyond what the fixtures already contain.
*Accept:* `AnthropicProvider` tests replay the real response shapes, with no network in CI.

**M1-4 Prompt and field-description tuning loop.** Make prompts versioned files (`packages/extract/prompts/*.md` with a version header), and record the version used on each `llm_calls` row. Add `evals/run_eval.py --compare promptA promptB`. Iterate until live invoices F1 is at least 0.95 after repair.
*Accept:* live F1 is at least 0.95 on invoices, auto-approved precision is at least 0.99, and every winning change is recorded with its numbers.

**M1-5 Prompt caching.** Cache the schema and field-description prefix, which repeats in every chunk. Measure cost per page before and after.
*Accept:* cost per page drops by a measured percentage, and F1 is unchanged.

**M1-6 Confidence calibration on real labels.** Once at least 300 fields are labelled (M2-2), fit the confidence weights per document class with logistic regression. Add a reliability diagram and expected calibration error to the eval report.
*Accept:* calibration error is under 0.05 on held-out data, and the auto-approve threshold is re-chosen from the curve.

**M1-7 Model routing review.** With live numbers, check each router rule (`packages/router`) against cost per correct field, and change thresholds where the data says to.
*Accept:* a routing table in the PR shows cost and F1 per rule, before and after.

---

## M2 · Real-document coverage

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M2-1 | Design-partner document intake | M | todo |
| M2-2 | Labelling tool and 300-field gold set | M | todo |
| M2-3 | Multi-record PDFs (statements, ledgers) | L | todo |
| M2-4 | Bank statements dataset | M | todo |
| M2-5 | Word-level evidence boxes | M | todo |
| M2-6 | Better OCR and layout | L | todo |
| M2-7 | Handwriting and checkbox detection | M | todo |
| M2-8 | Indian languages | M | todo |
| M2-9 | Real embedding model | S | todo |

**M2-1 Design-partner document intake.** Add a CLI (`uv run formwork-intake <folder> --tenant <id>`) that loads a partner's documents into a private eval dataset under `evals/private/`. That folder is git-ignored and documented. Hash file names, and never commit partner data.
*Accept:* a private dataset runs through `run_eval.py --dataset private/<name>`, and a `.gitignore` test proves the folder can't be committed.

**M2-2 Labelling tool and 300-field gold set.** Add a "label" mode to the review console that saves corrected values as `expected.json` for a private dataset. Label at least 300 fields across at least 3 document classes.
*Accept:* gold sets exist, and the eval runner reads them.

**M2-3 Multi-record PDFs.** The LLM path assumes one record per document today. Add record segmentation, so a statement or ledger PDF yields N records with the row-level evidence needed for entity keys. This changes the extraction contract, so it needs an ADR.
*Accept:* a synthetic 3-page statement with 60 transactions gives field F1 of at least 0.95, and existing evals don't regress.

**M2-4 Bank statements dataset.** Build `evals/datasets/bank_statements`, which the LLD lists but was never built: 10 synthetic statements in Indian bank formats, running balances and a rule that each balance equals the previous one plus credits minus debits.
*Accept:* the dataset is in the eval gate, and the running-balance rule catches seeded errors.

**M2-5 Word-level evidence boxes.** Evidence is currently block-level. Store word boxes from the text layer and from OCR, and cite the tightest span that contains the quote, so the review UI highlights the exact number.
*Accept:* at least 95% of evidence boxes on the invoice set cover only the quoted span, and the UI highlights tighter.

**M2-6 Better OCR and layout.** Add a pluggable `LayoutEngine` beside the current Tesseract path, and choose per document from a quality signal. Candidates, evaluated in this order:
1. **OCRia-nano-1** (LlamaIndex, 8.1M parameters, CPU, open weights through LiteParse: `pip install liteparse`, then `lit models pull ocria-nano`)
2. Docling
3. PaddleOCR

Notes for OCRia:
- It reports no OCR confidence. Derive a block confidence from character-pattern checks plus a Tesseract cross-check on numeric spans, and document the method, because routing depends on it.
- If it returns only block boxes, not word boxes, implement it as a `LayoutEngine` that returns `RawBlock`s directly.
- Handwriting and degraded scans stay on the vision tier.
- **Licence gate:** the weights are under a modified Open RAIL-M licence, and commercial use needs written terms from LlamaIndex. Evaluate freely, but don't make it the default until the terms are agreed.

Selection is via `FORMWORK_OCR_ENGINE=tesseract|ocria|docling|paddle`.
*Accept:* ADR 0004 records per-engine scanned-document F1, OCR-caused misses and CPU ms per page on `invoices` and `po_vs_invoice`. The default changes only if F1 improves, confidence is good enough for routing, and the licence is cleared.

**M2-7 Handwriting and checkbox detection.** Detect handwriting regions and checkboxes at ingest (`BlockType.HANDWRITING`, `CHECKBOX`), and route them to the vision tier.
*Accept:* the GRN scans and a synthetic form with checkboxes extract correctly.

**M2-8 Indian languages.** Add Hindi and Bengali OCR language packs, a script detector, and a test set of bilingual invoices.
*Accept:* bilingual invoices reach F1 of at least 0.90, and English-only F1 is unchanged.

**M2-9 Real embedding model.** Replace the hashing embedder with a real one, either local (for example `bge-small`) or an API, behind the existing interface, with the dimension set in config and a migration.
*Accept:* retrieval recall@12 improves on the private set, and evals don't regress.

---

## M3 · Deployable infrastructure

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M3-1 | Make docker compose run | S | todo |
| M3-2 | Real S3 / MinIO / Azure Blob storage | S | todo |
| M3-3 | Cloud deployment (Azure Container Apps) | L | todo |
| M3-4 | Migrations on deploy, backups, restore drill | M | todo |
| M3-5 | Autoscaling on queue depth | M | todo |
| M3-6 | Load test against the p95 target | M | todo |
| M3-7 | Presigned uploads and large files | M | todo |
| M3-8 | Retention scheduler | S | todo |

**M3-1 Make docker compose run.** None of the services have been built yet; the Docker setup and the three Dockerfiles were never run. Build and run them on your Mac, fix them, and add a compose smoke test to CI.
*Accept:* `docker compose up -d` and then one HTTP job end to end both work, and CI builds all images.

**M3-2 Real object storage.** Test the S3 store against MinIO in compose, and add an Azure Blob implementation, since your Azure background makes it the likely cloud.
*Accept:* integration tests pass against MinIO, and against Azurite for Blob.

**M3-3 Cloud deployment.** Add Bicep or Terraform for Azure Container Apps: API, worker, web, Azure Database for PostgreSQL Flexible Server with pgvector, Azure Cache for Redis, Blob storage, Key Vault and Front Door. Include separate staging and production stacks.
*Accept:* a deploy from GitHub Actions brings up staging, and a smoke job passes there.

**M3-4 Migrations on deploy, backups and a restore drill.** Run Alembic as a pre-deploy job, turn on point-in-time database backups, and write a restore runbook.
*Accept:* a restore drill on staging is documented with the time it took.

**M3-5 Autoscaling on queue depth.** Scale workers on Redis queue length, and give the OCR- and vision-heavy stages their own queue.
*Accept:* the load test (M3-6) shows workers scaling out and back in.

**M3-6 Load test.** Add a k6 or Locust scenario: 50 concurrent 10-page jobs.
*Accept:* p95 latency for a 10-page job is at most 60 s on the staging sizing, or a written gap analysis.

**M3-7 Presigned uploads and large files.** Add `POST /v1/files` with presigned upload registration, a streaming hash, a 200 MB limit and resumable chunks.
*Accept:* a 150 MB scanned PDF uploads and processes.

**M3-8 Retention scheduler.** Purge originals, page images and outputs on a schedule, per tenant retention settings, with an audit record.
*Accept:* a test proves files older than the retention period are gone and the audit rows exist.

---

## M4 · Security, privacy and legal

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M4-1 | Threat model | S | todo |
| M4-2 | Secrets, key rotation, webhook-secret rotation | M | todo |
| M4-3 | Audit log | M | todo |
| M4-4 | Upload malware scanning | S | todo |
| M4-5 | PII masking before LLM calls and in logs | M | todo |
| M4-6 | Webhook SSRF hardening | S | todo |
| M4-7 | Dependency and image scanning | S | todo |
| M4-8 | Legal pack | M | todo |
| M4-9 | DPDP Act readiness and data residency | M | todo |
| M4-10 | External pen test | M | todo |

**M4-1 Threat model.** Write `docs/security/threat-model.md` (STRIDE per component, covering prompt injection, tenant isolation and the webhooks), each threat linked to a control or a roadmap item.
*Accept:* every high-severity threat has a control or an item ID.

**M4-2 Secrets and rotation.** Move secrets to Key Vault. Allow two valid API keys per tenant during a rotation, and rotate webhook secrets with an overlap period.
*Accept:* rotation tests pass with zero downtime.

**M4-3 Audit log.** Add an append-only `audit_events` table recording key creation and revocation, schema changes, review decisions, exports and retention purges. Expose it at `GET /v1/audit`.
*Accept:* every listed action writes one row, and tenants can't read each other's rows (RLS test).

**M4-4 Upload malware scanning.** Scan every upload with ClamAV before parsing; flagged files are quarantined and fail with a clear error.
*Accept:* the EICAR test file is rejected, and clean files pass.

**M4-5 PII masking.** Run Presidio with Indian recognisers (PAN, Aadhaar, bank account and IFSC, phone) before LLM calls where the schema doesn't ask for those fields, and in `llm_calls.response` and the logs.
*Accept:* zero PII in `llm_calls` and the logs for the PII test set, and F1 is unchanged on fields that aren't masked.

**M4-6 Webhook SSRF hardening.** Resolve the hostname, then connect to the checked IP, so DNS rebinding can't get through. Block redirects to private ranges and add a per-tenant host allowlist option.
*Accept:* rebinding and redirect tests fail closed.

**M4-7 Dependency and image scanning.** Add pip-audit, npm audit and Trivy in CI, and Dependabot.
*Accept:* CI fails on high-severity CVEs, and the current baseline is clean or waived with reasons.

**M4-8 Legal pack.** Draft the terms of service, privacy policy, data processing agreement and sub-processor list (Anthropic, the cloud provider), plus the retention-policy wording. Claude can draft; a lawyer must review.
*Accept:* drafts in `docs/legal/`, marked "not reviewed" until counsel signs off.

**M4-9 DPDP Act readiness.** Add an India data-residency option (an Azure Central India region), consent and notice text, data-principal request handling (export and delete per tenant), and a breach notification runbook.
*Accept:* export and delete endpoints are tested, and the runbook exists.

**M4-10 External pen test.** Book it once M3 and M4-1 to M4-7 are done, then fix the findings.
*Accept:* the report is filed and all high and critical findings are closed.

---

## M5 · SaaS product: accounts, keys, billing

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M5-1 | User accounts and organisations | L | todo |
| M5-2 | API key management UI | M | todo |
| M5-3 | Usage metering | M | todo |
| M5-4 | Billing and plans | L | todo |
| M5-5 | Self-serve signup and onboarding | M | todo |
| M5-6 | SSO (SAML/OIDC) and RBAC | L | todo |
| M5-7 | Public docs site | M | todo |
| M5-8 | Status page and support | S | todo |

**M5-1 User accounts and organisations.** Add organisations, users and memberships (owner, admin, reviewer, viewer), using an auth provider (Clerk, Auth0 or Azure AD B2C) rather than building auth yourself. The console stops proxying a single key.
*Accept:* two organisations can't see each other's jobs through the UI or the API (end-to-end test), and roles are enforced on review actions.

**M5-2 API key management UI.** Create, name, scope (read, write, admin), rotate and revoke keys. A key is shown once, and its last use is tracked.
*Accept:* Playwright flow and API tests pass.

**M5-3 Usage metering.** Meter pages processed, LLM spend and storage per organisation per day, with a usage page in the console.
*Accept:* metered pages match the `documents` table, and the usage page reconciles with `llm_calls`.

**M5-4 Billing and plans.** Use Razorpay for India and Stripe for international, metered by page with included credits. The self-serve, growth and enterprise tiers follow the Reducto-style model in the plan. Enforce plan limits in the API.
*Accept:* a test-mode subscription, usage reported, an invoice generated, and an over-limit job refused with a clear error.

**M5-5 Self-serve signup and onboarding.** Signup leads to a sample job on the bundled AP dataset in under 2 minutes, then to "bring your own schema".
*Accept:* time to first result is measured in the Playwright flow.

**M5-6 SSO and RBAC.** Add SAML and OIDC for enterprise organisations, custom roles and SCIM later.
*Accept:* login works with an Azure AD test tenant.

**M5-7 Public docs site.** Add a docs site (Mintlify or Docusaurus) built from `docs/openapi.json`, with quickstarts in curl, Python SDK and TypeScript, and a schema-writing guide.
*Accept:* published from CI, with zero broken links.

**M5-8 Status page and support.** Add a hosted status page fed by health checks, and a support email and widget.
*Accept:* the status page reflects a staging outage drill.

---

## M6 · Extraction enhancements (product edge)

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M6-1 | Few-shot memory from corrections | M | todo |
| M6-2 | Schema compiler from sample documents | M | todo |
| M6-3 | Line-item tables at scale | M | todo |
| M6-4 | Custom rule builder | M | todo |
| M6-5 | Reconciliation templates | M | todo |
| M6-6 | Document classification and splitting | M | todo |
| M6-7 | Batch API path for cheap overnight jobs | S | todo |
| M6-8 | Schema versioning diff and re-run | S | todo |

**M6-1 Few-shot memory from corrections.** Approved corrections become per-tenant few-shot examples, chosen by document class and field. Tenant isolation is strict, and no example ever crosses tenants (poisoning test).
*Accept:* F1 on a tenant's repeat vendors improves by a measured amount, and the cross-tenant test passes.

**M6-2 Schema compiler from sample documents.** Upload 3 to 5 example documents with no schema; the compiler proposes fields, descriptions, entity keys and rules for the user to confirm.
*Accept:* on 3 document classes, the proposed schema needs at most 3 edits to reach the gold schema.

**M6-3 Line-item tables at scale.** Tables longer than 500 rows across pages, with header repeat detection, row continuity across pages, and totals reconciled against the line sum.
*Accept:* a synthetic 12-page, 800-line invoice reaches line-level F1 of at least 0.97.

**M6-4 Custom rule builder.** A UI and API for field and cross-record rules (simpleeval expressions with autocomplete and a live test against the current job).
*Accept:* a user-created rule flags a seeded error in the review queue.

**M6-5 Reconciliation templates.** Prebuilt schemas and rules for three-way match (PO, invoice and GRN), bank reconciliation and GST input-credit (GSTR-2B against the purchase register).
*Accept:* each template runs on its synthetic dataset in the eval gate.

**M6-6 Document classification and splitting.** Split merged PDFs (a scan of 30 mixed documents) into documents, and classify each before extraction.
*Accept:* split accuracy of at least 0.98 on a synthetic merged set.

**M6-7 Batch API path.** An option for non-urgent jobs to use the Message Batches API at 50% cost.
*Accept:* a batch job completes and costs about half per page.

**M6-8 Schema versioning diff and re-run.** Show the diff between schema versions, and re-run only the fields that changed on past jobs.
*Accept:* re-running after changing one field description costs less than 20% of a full run.

---

## M7 · Review UX

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M7-1 | Usability sessions and the 20-fields-in-3-minutes target | S | todo |
| M7-2 | Accessibility audit | M | todo |
| M7-3 | Bulk actions and saved filters | S | todo |
| M7-4 | Multi-user review with assignment and locks | M | todo |
| M7-5 | Comments on fields | S | todo |
| M7-6 | Live run screen over SSE | S | todo |
| M7-7 | Lineage graph view on Review | S | done |

**M7-1 Usability sessions.** Run 5 timed sessions with real AP reviewers, and log time per field and errors.
*Accept:* the median clears 20 fields in under 3 minutes, or the findings become new M7 items.

**M7-2 Accessibility audit.** Test with axe, VoiceOver and NVDA, keyboard-only. Every review state must be readable without colour.
*Accept:* zero serious axe violations, and the screen-reader findings are fixed.

**M7-3 Bulk actions and saved filters.** Approve all above a threshold per field, filter by vendor, document class or status, and save views.
*Accept:* Playwright flow passes.

**M7-4 Multi-user review.** Assign queues to reviewers, lock a field while one person edits it, and show presence.
*Accept:* two-browser test shows no lost edits.

**M7-5 Comments on fields.** Add threaded comments on any extraction, with mentions and an audit trail.
*Accept:* comments show up in the lineage export.

**M7-6 Live run screen.** Stream job progress over Server-Sent Events, with the WebSocket as a fallback, instead of polling.
*Accept:* the Run screen updates within 1 s of each worker event.

**M7-7 Lineage graph view.** Add a `Grid`/`Lineage` toggle to Review: a node-link graph of source documents → merged record → resolved fields, color-coded by status, click-to-highlight tracing a value back to its winning source. Reuses the existing inspector, Approve/Edit/Next-issue actions and keyboard shortcuts unchanged; no new dependency or endpoint, built entirely from data `ReviewView` already fetches.
*Accept:* done — the Playwright flow (`apps/web/e2e/test_flows.py`) covers the toggle, a field click and record switching at 1440 px and 390 px, with screenshots in `apps/web/e2e/screenshots/lineage-*.png`.

---

## M8 · Integrations

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M8-1 | Formwork MCP server | M | todo |
| M8-2 | Source connectors: S3, SharePoint/OneDrive, Gmail/Outlook | L | todo |
| M8-3 | Destinations: Postgres, Snowflake, Fabric/OneLake, Google Sheets | L | todo |
| M8-4 | TypeScript SDK | S | todo |
| M8-5 | Zapier / n8n / Power Automate actions | M | todo |

**M8-1 Formwork MCP server.** Add `apps/mcp` exposing `compile_schema`, `start_job`, `get_job`, `get_review_queue`, `approve_field` and `export`, authenticated by API key.
*Accept:* Claude Desktop runs a full job through it (screen recording in the PR).

**M8-2 Source connectors.** Incremental sync with per-connector cursors. Mailbox ingestion turns attachments into jobs automatically.
*Accept:* new files in a watched folder or inbox create a job within 5 minutes.

**M8-3 Destinations.** Push approved records to a warehouse table, with lineage columns. A Microsoft Fabric/OneLake destination fits your Fabric background and gives a distribution path.
*Accept:* records land in each destination, and re-runs update rows instead of duplicating them.

**M8-4 TypeScript SDK.** Generated from OpenAPI, with webhook verification helpers.
*Accept:* published to npm under a test tag, and the example script runs.

**M8-5 Automation-platform actions.** Actions for "start job" and "job completed".
*Accept:* a working template on each platform.

---

## M9 · Observability and LLMOps

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M9-1 | Collector, dashboards, alerts | M | todo |
| M9-2 | LLM tracing (Langfuse or LangSmith) | S | todo |
| M9-3 | Multi-provider routing and failover (LiteLLM) | M | todo |
| M9-4 | Prompt A/B and staged rollouts | M | todo |
| M9-5 | Cost and latency dashboard per tenant | S | todo |

**M9-1 Collector, dashboards and alerts.** Send OpenTelemetry to Azure Monitor or Grafana Cloud. Build dashboards for job throughput, stage latency, error rate, queue depth and cost per page, with alerts on SLO burn.
*Accept:* an alert fires in a staging failure drill.

**M9-2 LLM tracing.** Trace every LLM call with prompt version, tokens, cost and latency, linked to the job and the field.
*Accept:* one job's full trace can be opened from the job page.

**M9-3 Multi-provider routing and failover.** Use LiteLLM behind `ModelProvider` for a second provider and automatic failover.
*Accept:* a forced provider outage in staging completes jobs on the fallback.

**M9-4 Prompt A/B and staged rollouts.** Roll out a new prompt version to X% of tenants, compare eval and production metrics, and promote or roll back from the console.
*Accept:* a rollout and a rollback are demonstrated on staging.

**M9-5 Cost and latency dashboard per tenant.** An internal admin view of margin per tenant: revenue per page against LLM and infrastructure cost.
*Accept:* the numbers reconcile with billing (M5-4).

---

## M10 · Learning labs (IIT Delhi Agentic AI course)

These run alongside the course from January 2027; full detail is in the "Learning track" section of `docs/lld.md`. Labs live in `labs/` and graduate into the product only when they beat the pipeline on the eval gate. Several overlap with items above: M8-1 is the Module 3 lab, M4-5 is Module 6, M9-3 is Module 8 and M7-6 is Module 7. Do the product item and write the lab README for it at the same time.

| ID | Lab | Course module | Status |
| --- | --- | --- | --- |
| L1 | Workflow vs agent ADR + toy LangGraph | 1 | todo |
| L2 | LCEL schema compiler, prompt A/B, injection pack | 2 | todo |
| L3 | Self-directed repair agent + MCP server (M8-1) | 3 | todo |
| L4 | Doc Q&A with citations, vector DB benchmark, GraphRAG, NL-to-SQL | 4 | todo |
| L5 | Pipeline as LangGraph StateGraph; CrewAI schema onboarding crew | 5 | todo |
| L6 | Presidio (M4-5), DeepEval faithfulness, calibration (M1-6), poisoning tests | 6 | todo |
| L7 | SSE/WebSocket streaming (M7-6), LangSmith comparison | 7 | todo |
| L8 | LiteLLM failover (M9-3), MLflow prompt A/B, Streamlit cost dashboard, ROI model | 8 | todo |

Lab prompt:

```text
Read CLAUDE.md, docs/lld.md (Learning track, Module <N> row) and docs/ROADMAP.md (item <L#>).
Build labs/<NN-name>. Reuse packages/core models and evals datasets; do not modify packages/ or apps/ unless the item says it graduates.
Write a README that explains the concept, leaving TODO markers where I should write in my own words, plus the run command and an eval comparison against the current pipeline.
Finish with docs/adr/<NNN>-<name>.md: what we tried, the numbers, and whether it graduates.
```

---

## M11 · Transformation layer (Power Query–style recipes)

Customers reshape extracted data before using it: renaming, mapping to master data, calculated columns, unpivoting line items, joins with reference tables. Doing it inside Formwork keeps the trail back to the source document unbroken. New package: `packages/transform`, which runs between validate and output.

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M11-1 | Recipe model and core steps | M | todo |
| M11-2 | Lineage through transforms | M | todo |
| M11-3 | Recipe editor UI (applied steps + preview grid) | L | todo |
| M11-4 | Mapping tables and lookups | M | todo |
| M11-5 | AI-suggested steps | M | todo |
| M11-6 | Export recipe as Power Query M | M | todo |
| M11-7 | Cross-job append and scheduled recipes | M | todo |

**M11-1 Recipe model and core steps.** A `TransformRecipe` is an ordered list of typed JSON steps, versioned with the schema, and never free code. The steps are: rename, reorder, change type, filter rows, add a column (simpleeval expression), split and merge columns, replace values, group and aggregate, pivot and unpivot, and deduplicate. Execute with Polars lazy frames. Adding fields to the models needs an ADR.
*Accept:* every step type has unit tests, a 100,000-row recipe runs in under 2 s, and a recipe that references an unknown column fails with a clear error naming the step.

**M11-2 Lineage through transforms.** Each output cell records the input cells it came from. A rename carries its source cell, a calculated column lists its inputs, and an aggregate lists every row in its group. `lineage.json` and the review UI resolve a transformed cell back to the source document region.
*Accept:* clicking a calculated or aggregated cell in the UI opens the highlighted source regions, and tests cover each step type.

**M11-3 Recipe editor UI.** An applied-steps panel (add, reorder, edit, delete, disable), a data preview grid, profiles per column (blanks, distinct values, errors) and a formula bar, modelled on the Power Query editor.
*Accept:* Playwright flow builds a 5-step recipe and the preview updates within 1 s on 10,000 rows, at 1440 px and 390 px.

**M11-4 Mapping tables and lookups.** Customers upload reference tables (vendor master, GL codes, cost centres) as CSV or Excel. A lookup step supports exact and fuzzy matching, and sends unmatched values to review.
*Accept:* mapping vendor names to vendor master IDs on the AP dataset has 0 wrong matches, and unmatched names appear as review items.

**M11-5 AI-suggested steps.** A plain-English request such as "add days overdue from invoice date and payment terms" becomes proposed steps chosen from the fixed step list only, shown as a preview the user accepts or edits. Model calls go through `ModelProvider`.
*Accept:* 10 scripted requests produce valid recipes with no free code, and an invalid proposal is rejected with a clear message.

**M11-6 Export recipe as Power Query M.** Generate equivalent M code for a recipe, so Power BI and Fabric users can reuse the logic.
*Accept:* the generated M for the 5-step recipe runs in Power BI Desktop and produces the same rows (manual check, documented with a screenshot).

**M11-7 Cross-job append and scheduled recipes.** A recipe can append results across jobs, such as the month's invoices, and run automatically when a job completes.
*Accept:* three jobs append into one dataset with no duplicate rows, and lineage stays per job.

---

## M12 · RAG

Formwork's parsing already produces clean, cited chunks. Lead with RAG ingestion; questions across documents and extracted data come next. A generic hosted RAG service isn't planned.

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M12-1 | Real embedding model (same as M2-9) | S | todo |
| M12-2 | Chunking options on /v1/parse | M | todo |
| M12-3 | Embedding-optimised table text | S | todo |
| M12-4 | Vector DB connectors and framework loaders | M | todo |
| M12-5 | /v1/query: grounded answers with citations | L | todo |
| M12-6 | Questions across documents and extracted data | L | todo |

**M12-2 Chunking options.** `/v1/parse` takes `chunking: block | page | section | size` with a target size and overlap. Small blocks merge up to the target size, and tables are never split. Every chunk keeps its page boxes.
*Accept:* tests cover each mode, and no chunk splits a table or crosses a page boundary without recording it.

**M12-3 Embedding-optimised table text.** Each chunk gets an extra `embed_text` field in which tables are written as short sentences, because those match search queries better than markdown tables.
*Accept:* retrieval recall@5 on a table-question set improves compared with markdown alone.

**M12-4 Connectors and loaders.** Push chunks with their metadata to pgvector, Azure AI Search and Pinecone, and publish a LangChain document loader and a LlamaIndex reader.
*Accept:* a script in `examples/` loads a document into each target and runs a query that returns cited chunks.

**M12-5 /v1/query.** Retrieve with the existing hybrid search, then have the strong model answer, citing block IDs. Every sentence must pass the grounding check, or it's dropped and the answer says what couldn't be supported. Results are filtered per tenant and per collection.
*Accept:* on a 50-question set, at least 95% of answer sentences are grounded, and a prompt-injection document can't change an answer.

**M12-6 Questions across documents and data.** Route each question to natural-language SQL over extracted records, to document retrieval, or to both. The answer cites records and documents, and the SQL is restricted to read-only queries on the tenant's own views.
*Accept:* "which vendors were short-shipped in Q3 and what did their emails say" is answered correctly on the AP dataset, with citations for both parts.

---

## M13 · Quality modes, plans and deployment options

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M13-1 | Quality modes: Economy, Standard, Precision | M | todo |
| M13-2 | Cost estimate before running | S | todo |
| M13-3 | Published eval numbers per mode | S | todo |
| M13-4 | Plan-based limits and mode pricing | M | todo |
| M13-5 | Bring your own model key | M | todo |
| M13-6 | Enterprise deployment package | L | todo |

**M13-1 Quality modes.** Each mode maps to existing routing settings:
- **Economy:** cheap model wherever it can cope; strong model only for unreadable regions; 1 repair attempt; Batch API allowed.
- **Standard:** today's defaults.
- **Precision:** strong model on every block, 2 repair attempts, and two extractors must agree on key fields.

A mode can be set per schema, per job and per field. Grounding, rules and review are the same in every mode.
*Accept:* each mode runs on the eval sets, with F1 and cost per page reported for each.

**M13-2 Cost estimate before running.** `POST /v1/jobs?dry_run=true` and the console show the estimated cost from page count, mode and document mix before the job starts.
*Accept:* estimates fall within ±20% of actual cost on the eval sets.

**M13-3 Published numbers per mode.** A generated docs page shows F1, auto-approve rate and cost per page for each mode on the public synthetic datasets.
*Accept:* the page is regenerated by the nightly eval.

**M13-4 Plan limits and pricing.** Credits per page vary by mode, plans control which modes are available, and the limits are enforced in the API. This builds on M5-3 and M5-4.
*Accept:* a job in Precision mode on a plan without it is refused with a clear message, and usage is billed at the mode's rate.

**M13-5 Bring your own model key.** Enterprise tenants can supply their own Anthropic, AWS Bedrock or Google Vertex AI credentials, stored in Key Vault. Usage is metered but model cost isn't billed by Formwork.
*Accept:* a tenant with its own key runs a job that bills to their account, and the key never appears in logs or API responses.

**M13-6 Enterprise deployment package.** Infrastructure as code that installs Formwork into a customer's Azure or AWS account, plus a licence-key check, an update path for installs without internet access, and a guide to the hybrid option (customer storage, Formwork compute). Build it only when 2 or 3 serious prospects ask; it builds on M3-3.
*Accept:* a clean install in a test subscription runs an end-to-end job, and the licence expiry is enforced.

---

## M14 · Insights

These are insights only Formwork has: what the documents say, where they disagree and what that's worth, traced to evidence. General BI stays in Power BI (M15).

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M14-1 | Insight data layer | M | todo |
| M14-2 | AP template: exceptions and money at risk | M | todo |
| M14-3 | Vendor scorecards and payables ageing | M | todo |
| M14-4 | Process health page | S | todo |
| M14-5 | Chart-to-evidence drill-through | M | todo |
| M14-6 | Cited summaries and alerts | M | todo |

**M14-1 Insight data layer.** Tenant-scoped SQL views (or DuckDB) over records, extractions and job history: exceptions by type, value held, vendor history and due dates.
*Accept:* the views are covered by RLS tests, and a query on 100,000 records returns in under 1 s.

**M14-2 AP exceptions and money at risk.** Charts in the console (ECharts or Recharts) cover open conflicts, short shipments, price mismatches against the PO, duplicate invoices, failed rules, and GST credit at risk from invalid GSTINs. Each is shown by vendor and by age.
*Accept:* numbers reconcile with the review queue, and Playwright screenshots are attached at both widths.

**M14-3 Vendor scorecards and ageing.** Per vendor: short-shipment rate, invoice error rate, frequency of revised invoices, and price drift. Payables are aged by due date, with early-payment discount windows.
*Accept:* the scorecards match hand-computed values on the PO-vs-invoice dataset.

**M14-4 Process health.** Documents processed, auto-approve rate, review time, cost per page and the accuracy trend.
*Accept:* figures match `llm_calls` and the review logs.

**M14-5 Chart-to-evidence drill-through.** Clicking any bar or point lists the records behind it, and clicking a record opens the highlighted source regions.
*Accept:* the Playwright flow goes from chart to record to highlighted page.

**M14-6 Cited summaries and alerts.** A weekly plain-language summary in which every claim links to its records and passes the grounding check. Alerts (duplicate invoice, vendor error rate above a threshold) go out by email, Slack or webhook.
*Accept:* summary claims all resolve to records, and an alert fires in a test.

---

## M15 · Power BI and Fabric delivery

**M15-0 must be done before anything else in this milestone.** It's an employment check, not a build item: Power BI and Fabric are the day job's market. Review the employment agreement (IP assignment, outside work, competing products) and decide whether to raise the project with the employer.

Design principle: use the Power BI **Modeling MCP server** and Microsoft's Power BI agent skills **at design time** to author templates. When the product runs, deploy with the **Fabric REST APIs**, which is deterministic and testable. Don't run MCP per customer inside the backend: it's a local tool in public preview, and it can't edit reports.

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M15-0 | Employment and IP check | S | todo |
| M15-1 | Author AP semantic model and report templates | M | todo |
| M15-2 | Download Power BI project (PBIP) | M | todo |
| M15-3 | OneLake destination (Delta tables) | M | todo |
| M15-4 | Automatic Direct Lake model and report deployment | L | todo |
| M15-5 | Evidence links inside Power BI | S | todo |
| M15-6 | Questions through the remote Power BI MCP server | M | todo |

**M15-1 Author templates.** In a Claude Code session with the Power BI Modeling MCP (`npx -y @microsoft/powerbi-modeling-mcp@latest --start`) and Microsoft's semantic-model and report-authoring skills, build a star-schema AP model in TMDL. It has a records fact table, a lineage table, an exceptions table, and vendor and date dimensions. Measures cover total invoiced, money held, short-shipment rate, GST credit at risk and auto-approve rate. Add a PBIR report with pages for exceptions, money at risk, ageing and vendor scorecards. Commit both to `templates/powerbi/` with placeholders for the schema's fields.
*Accept:* the templates open in Power BI Desktop against sample data, and a Best Practice Analyzer run reports no errors.

**M15-2 Download a PBIP.** Fill in the templates from a tenant's schema and give a downloadable PBIP that points at a Formwork export. It needs no Fabric capacity.
*Accept:* the downloaded project opens and refreshes in Power BI Desktop for 2 different schemas.

**M15-3 OneLake destination.** Write records, lineage and exceptions as Delta tables to a customer's lakehouse, using a service principal they authorise. Re-runs update rows instead of duplicating them. This is the same item as M8-3 for Fabric.
*Accept:* a job lands in a test lakehouse, and a re-run updates rows in place.

**M15-4 Automatic deployment.** Create or update a Direct Lake semantic model and a report in the customer's workspace with the Fabric REST APIs, using the filled-in TMDL and PBIR definitions.
*Accept:* deployment to a test workspace succeeds, the report shows the job's data without a manual refresh, and a redeploy is idempotent.

**M15-5 Evidence links.** Each record row carries a URL to Formwork's evidence viewer, set up as a web-URL column. Power BI drill-through opens the highlighted source page.
*Accept:* clicking from a Power BI visual opens the right highlighted region.

**M15-6 Questions through the remote MCP server.** Formwork's assistant answers questions over the customer's deployed model through the remote Power BI MCP server, and links back to evidence.
*Accept:* 10 scripted questions are answered from the model, with record links.

---

## M16 · Export formats

| ID | Item | Size | Status |
| --- | --- | --- | --- |
| M16-1 | Evidence pack (.zip with PDF audit report) | M | todo |
| M16-2 | Excel with lineage sheet | S | todo |
| M16-3 | Tally XML vouchers | M | todo |
| M16-4 | Parquet and NDJSON | S | todo |
| M16-5 | Push to Google Sheets and Excel Online | M | todo |
| M16-6 | Form filling from extracted data | L | todo |

**M16-1 Evidence pack.** A single download containing the dataset, `lineage.json`, an exceptions report, and a PDF audit report showing each flagged value beside its highlighted source crop.
*Accept:* the pack for the AP dataset opens cleanly, and every flagged value in the PDF shows its crop.

**M16-2 Excel lineage sheet.** A second sheet listing source file, page, quote, confidence and status for every cell, alongside the existing review highlighting.
*Accept:* the sheet row count equals the number of fields, and the workbook opens in Excel and LibreOffice.

**M16-3 Tally XML.** Export purchase vouchers that import into Tally Prime, with the ledger mapping configured per tenant (this can use M11-4 mapping tables).
*Accept:* the generated XML imports into a Tally Prime test company without errors (manual check, documented).

**M16-4 Parquet and NDJSON.** Typed exports for data teams, with lineage as a separate file or as nested columns.
*Accept:* the Parquet file reads in Polars and DuckDB with the correct types.

**M16-5 Sheets push.** Write results to a Google Sheet or an Excel Online workbook the user picks, and update the same rows on re-run.
*Accept:* a re-run updates rows instead of appending duplicates.

**M16-6 Form filling.** Fill a target PDF or DOCX form (payment voucher, claim form, GST worksheet) from approved records, detecting the fields with the strong model and mapping them through a saved field map. Build this only once customers ask for it.
*Accept:* 3 sample forms are filled correctly, and every filled value links to its lineage.

---

## Slash command

Save as `.claude/commands/roadmap.md` so `/roadmap M1-4` starts a session on that item:

```markdown
---
description: Work on one item from docs/ROADMAP.md
argument-hint: <item-id>
---
Read CLAUDE.md, PROGRESS.md and docs/ROADMAP.md. Work on roadmap item $ARGUMENTS only.
Start in plan mode: list the files you will touch, the tests you will add, and how you will prove each acceptance criterion.
Stay inside the packages the item names; ask before touching others. Model changes need an ADR.
When done: the ruff, mypy and pytest bar passes and the eval gate does not regress.
Update the item's status in docs/ROADMAP.md, add a line to PROGRESS.md, and commit with a conventional message (no Co-Authored-By or Claude-Session trailers).
```

---

## Keeping this file honest

- Change an item's status in the same PR that changes the code.
- When an item turns out bigger than its size, split it into lettered sub-items (M2-3a, M2-3b) before continuing.
- Add new ideas at the bottom of the relevant milestone with status `todo` and a one-line "why". Don't start them until they're placed on the critical path or after launch.
- Revisit the critical path after M1: live numbers may reorder M2 and M6.
