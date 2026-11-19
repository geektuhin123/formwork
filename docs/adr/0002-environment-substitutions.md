# ADR 0002: Substitutions against the LLD stack

Status: accepted (Phase 0-1)

| LLD | Built | Why | How to switch |
| --- | --- | --- | --- |
| Docling layout + PaddleOCR | Tesseract (`pytesseract`) with a geometric layout pass, behind `OcrEngine` | Docling/Paddle pull in PyTorch/PaddlePaddle (multi-GB, GPU-leaning). Tesseract meets the "under 6 s a page on CPU" bar. | Implement `OcrEngine` for Docling/Paddle and set it in `formwork_ingest.ocr.default_engine` |
| Embedding model (unspecified) | `HashingEmbedder`, 1024-d feature hashing | Anthropic has no embeddings endpoint; no extra vendor or model download in the MVP | Implement `Embedder` (Voyage, a local sentence-transformer) |
| Langfuse SDK | OpenTelemetry spans with `gen_ai.*` attributes | Langfuse ingests OTLP; one instrumentation path | Set `OTEL_EXPORTER_OTLP_ENDPOINT` |
| pgvector retrieval in SQL | Embeddings are stored in pgvector (HNSW index) but per-document retrieval scores blocks in process | A document's blocks fit in memory; cross-document search is not an MVP feature | Query `blocks.embedding` / `blocks.tsv` |
| Python 3.12 | `requires-python >=3.12`; developed on 3.13, images use 3.12 | Sandbox ships 3.13 | - |
| `packages/` list | Added `pipeline` (stage orchestration shared by worker and evals), `testkit` (synthetic fixtures, temp Postgres), `sdk` | Evals must run the same stage code as the worker without Redis | - |
| Extra tables | `job_tasks` (idempotency keys), `review_gaps`, `webhook_deliveries`; extra columns on `tenants`, `api_keys`, `documents`, `jobs`, `extractions`, `llm_calls`, `eval_cases` | Needed for resumability, review of missing fields, webhook retries, the per-job LLM response cache | - |
