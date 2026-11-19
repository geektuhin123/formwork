"""REST API under /v1 (docs/lld.md, "API design")."""

from __future__ import annotations

import ast
import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile
from formwork_core.context import JobContext
from formwork_core.db import TenantRepo, tenant_tx
from formwork_core.errors import FileTooLarge, NotFound, ValidationFailed
from formwork_core.groups import field_groups
from formwork_core.ids import new_id
from formwork_core.models import ExtractionStatus, FieldType, JobStatus, OutputFormat, TargetSchema
from formwork_core.storage import file_key, page_key
from formwork_ingest import ingest_file, validate_upload
from formwork_output import CONTENT_TYPES, build_lineage, render
from formwork_pipeline.job import REVIEW_STATUSES
from formwork_pipeline.webhooks import validate_webhook_url
from formwork_schema_compiler import (
    JsonSchemaSource,
    LLMSchemaCompiler,
    PromptSource,
    SampleFileSource,
    weak_descriptions,
)
from formwork_validate.rules import MAX_RULE_LENGTH

from formwork_api.auth import Principal, authenticate
from formwork_api.review import apply_review, field_item, gap_item
from formwork_api.schemas import (
    CompileRequest,
    CompileResponse,
    FileOut,
    JobCreate,
    JobOut,
    ReviewAction,
    ReviewResult,
    SchemaIn,
    SchemaSummary,
)

router = APIRouter(prefix="/v1")
Auth = Annotated[Principal, Depends(authenticate)]


def fw(request: Request) -> Any:
    return request.app.state.fw


async def _read_capped(upload: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise FileTooLarge(f"file exceeds the {limit // (1024 * 1024)} MB limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _check_schema(schema: TargetSchema) -> None:
    leaves = {f.path: f for f in schema.leaf_fields()}
    for key in schema.entity_key:
        if key not in leaves or "[]" in key:
            raise ValidationFailed(
                f"entity_key field {key!r} must be a top-level (non-repeating) field of the schema"
            )
    for f in leaves.values():
        for rule in f.rules:
            if len(rule) > MAX_RULE_LENGTH:
                raise ValidationFailed(f"rule on {f.path} is too long")
            try:
                ast.parse(rule, mode="eval")
            except SyntaxError as exc:
                raise ValidationFailed(f"rule on {f.path} is not a valid expression: {rule!r}") from exc
        if f.type in (FieldType.ARRAY, FieldType.OBJECT):
            raise ValidationFailed(f"{f.path}: {f.type.value} fields need children")


def _compile_response(schema: TargetSchema) -> CompileResponse:
    return CompileResponse.model_validate(
        {"schema": schema, "warnings": [w.model_dump() for w in weak_descriptions(schema)]}
    )


# ------------------------------------------------------------------ account
@router.get("/account", tags=["account"])
async def account(request: Request, who: Auth) -> dict[str, Any]:
    """The calling tenant, including the secret used to sign webhooks."""
    s = fw(request)
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        t = await TenantRepo(conn, who.tenant_id).tenant()
    return {
        "tenant_id": t["id"],
        "name": t["name"],
        "plan": t["plan"],
        "webhook_secret": t["webhook_secret"],
        "settings": t["settings"],
        "models": {"live": s.settings.live_models, "provider": s.provider.name},
    }


# ------------------------------------------------------------------ schemas
@router.post(
    "/schemas/compile", response_model=CompileResponse, response_model_by_alias=True, tags=["schemas"]
)
async def compile_schema(request: Request, body: CompileRequest, who: Auth) -> CompileResponse:
    """Draft a TargetSchema from a JSON Schema or a natural-language description. Nothing is saved."""
    compiler = LLMSchemaCompiler(fw(request).metered(who.tenant_id), who.tenant_id)
    src = (
        JsonSchemaSource(json_schema=body.json_schema or {}, name=body.name)
        if body.source == "json_schema"
        else PromptSource(prompt=body.prompt or "", name=body.name)
    )
    return _compile_response(await compiler.compile(src))


@router.post(
    "/schemas/compile/sample", response_model=CompileResponse, response_model_by_alias=True, tags=["schemas"]
)
async def compile_schema_from_sample(
    request: Request, who: Auth, file: Annotated[UploadFile, File()]
) -> CompileResponse:
    """Draft a TargetSchema from a sample output file (CSV, XLSX or JSON). Nothing is saved."""
    data = await _read_capped(file, 20 * 1024 * 1024)
    compiler = LLMSchemaCompiler(fw(request).metered(who.tenant_id), who.tenant_id)
    return _compile_response(
        await compiler.compile(SampleFileSource(filename=file.filename or "sample.csv", data=data))
    )


async def _save_schema(request: Request, who: Principal, body: SchemaIn, schema_id: str) -> CompileResponse:
    schema = TargetSchema(id=schema_id, tenant_id=who.tenant_id, version=0, **body.model_dump())
    _check_schema(schema)
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        if not schema_id.startswith("new:"):
            await repo.get_schema(schema_id)  # 404 if the id is unknown to this tenant
        saved = await repo.save_schema(schema)
    return _compile_response(saved)


@router.post(
    "/schemas",
    response_model=CompileResponse,
    response_model_by_alias=True,
    status_code=201,
    tags=["schemas"],
)
async def create_schema(request: Request, body: SchemaIn, who: Auth) -> CompileResponse:
    """Save a confirmed schema as version 1."""
    schema = TargetSchema(id=new_id("sch"), tenant_id=who.tenant_id, version=0, **body.model_dump())
    _check_schema(schema)
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        saved = await TenantRepo(conn, who.tenant_id).save_schema(schema)
    return _compile_response(saved)


@router.put(
    "/schemas/{schema_id}", response_model=CompileResponse, response_model_by_alias=True, tags=["schemas"]
)
async def update_schema(request: Request, schema_id: str, body: SchemaIn, who: Auth) -> CompileResponse:
    """Save a new version. Jobs pin the version they started with, so past results never change."""
    return await _save_schema(request, who, body, schema_id)


@router.get("/schemas", response_model=list[SchemaSummary], tags=["schemas"])
async def list_schemas(request: Request, who: Auth) -> list[SchemaSummary]:
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        rows = await TenantRepo(conn, who.tenant_id).list_schemas()
    out = []
    for r in rows:
        s = TargetSchema.model_validate(r["spec"])
        out.append(
            SchemaSummary(
                id=r["id"],
                version=r["version"],
                name=r["name"],
                field_count=len(s.leaf_fields()),
                weak_descriptions=len(weak_descriptions(s)),
                created_at=r["created_at"],
            )
        )
    return out


@router.get(
    "/schemas/{schema_id}", response_model=CompileResponse, response_model_by_alias=True, tags=["schemas"]
)
async def get_schema(
    request: Request, schema_id: str, who: Auth, version: Annotated[int | None, Query(ge=1)] = None
) -> CompileResponse:
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        return _compile_response(await TenantRepo(conn, who.tenant_id).get_schema(schema_id, version))


# ------------------------------------------------------------------ files
@router.post("/files", response_model=FileOut, status_code=201, tags=["files"])
async def upload_file(request: Request, who: Auth, file: Annotated[UploadFile, File()]) -> FileOut:
    """Upload one source file (multipart). The type is detected from content, not from the name."""
    s = fw(request)
    data = await _read_capped(file, s.settings.max_upload_bytes)
    stored = validate_upload(file.filename or "upload", data, s.settings.max_upload_bytes)
    ext = stored.filename.rsplit(".", 1)[-1].lower() if "." in stored.filename else "bin"
    key = file_key(who.tenant_id, stored.sha256, ext if ext.isalnum() and len(ext) <= 5 else "bin")
    if not await s.store.exists(key):
        await s.store.put(key, data, stored.mime_type)
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        row, created = await TenantRepo(conn, who.tenant_id).upsert_file(
            stored.filename, stored.mime_type, stored.sha256, len(data), key
        )
    return FileOut(
        file_id=row["id"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        deduplicated=not created,
    )


@router.get("/files/{file_id}/content", tags=["files"])
async def file_content(request: Request, file_id: str, who: Auth) -> Response:
    s = fw(request)
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        row = await TenantRepo(conn, who.tenant_id).get_file(file_id)
    data = await s.store.get(row["storage_key"])
    return Response(
        data,
        media_type=row["mime_type"],
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


@router.post("/parse", tags=["files"])
async def parse_only(request: Request, who: Auth, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """Parse only: returns the CanonicalDocument(s) for a file (an email returns its attachments too). Nothing is stored."""
    s = fw(request)
    from formwork_core.storage import MemoryObjectStore

    data = await _read_capped(file, s.settings.max_upload_bytes)
    stored = validate_upload(file.filename or "upload", data, s.settings.max_upload_bytes)
    ctx = JobContext(
        tenant_id=who.tenant_id,
        job_id="parse",
        provider=s.provider,
        store=MemoryObjectStore(),
        settings=s.settings,
    )
    docs = await ingest_file(stored, ctx)
    dumped = [
        d.document.model_dump(mode="json", exclude={"blocks": {"__all__": {"embedding"}}}) for d in docs
    ]
    return {"document": dumped[0], "attachments": dumped[1:]}


# ------------------------------------------------------------------ jobs
def _job_out(job: dict[str, Any], docs: list[str], tasks: list[dict[str, Any]], groups: int) -> JobOut:
    done = sum(1 for t in tasks if t["status"] == "done")
    total = len(job["file_ids"]) + max(len(docs), len(job["file_ids"])) * max(groups, 1) + 3
    stage = max(
        (t["stage"] for t in tasks),
        key=["ingest", "extract", "merge", "validate", "output"].index,
        default=None,
    )
    return JobOut(
        id=job["id"],
        status=job["status"],
        schema_id=job["schema_id"],
        schema_version=job["schema_version"],
        file_ids=job["file_ids"],
        document_ids=docs,
        cost_usd=job["cost_usd"],
        budget_usd=job["options"].get("budget_usd"),
        stats=job["stats"] or {},
        progress={"tasks_done": done, "tasks_total": max(total, done), "last_stage": stage},
        options=job["options"],
        error=job["error"],
        created_at=job["created_at"],
        finished_at=job["finished_at"],
    )


@router.post("/jobs", response_model=JobOut, status_code=202, tags=["jobs"])
async def create_job(
    request: Request,
    body: JobCreate,
    who: Auth,
    idempotency_key: Annotated[str | None, Header(max_length=200)] = None,
) -> JobOut:
    """Start a job: schema + files. Poll GET /v1/jobs/{id} or pass options.webhook_url."""
    s = fw(request)
    if len(set(body.file_ids)) > s.settings.max_files_per_job:
        raise ValidationFailed(f"a job takes at most {s.settings.max_files_per_job} files")
    options = body.options.model_dump(exclude_none=True)
    if body.options.webhook_url:
        validate_webhook_url(body.options.webhook_url, s.settings)
    options.setdefault("budget_usd", s.settings.default_budget_usd)
    if options["budget_usd"] > s.settings.max_budget_usd:
        raise ValidationFailed(f"budget_usd may not exceed {s.settings.max_budget_usd}")
    options.setdefault("auto_approve_threshold", s.settings.auto_approve_threshold)
    if idempotency_key:
        options["idempotency_key"] = hashlib.sha256(idempotency_key.encode()).hexdigest()
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        if idempotency_key:
            existing = await repo.find_job_by_idempotency_key(options["idempotency_key"])
            if existing:
                return _job_out(
                    existing, await repo.job_document_ids(existing["id"]), await repo.tasks(existing["id"]), 1
                )
        schema = await repo.get_schema(body.schema_id, body.schema_version)
        file_ids = list(dict.fromkeys(body.file_ids))
        for f in file_ids:
            await repo.get_file(f)
        job = await repo.create_job(schema.id, schema.version, file_ids, options)
    await s.queue.enqueue_job(who.tenant_id, job["id"])
    return _job_out(job, [], [], len(field_groups(schema)))


@router.get("/jobs", response_model=list[JobOut], tags=["jobs"])
async def list_jobs(
    request: Request, who: Auth, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[JobOut]:
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        rows = await TenantRepo(conn, who.tenant_id).list_jobs(limit)
    return [_job_out(j, [], [], 1) for j in rows]


async def _job_bundle(request: Request, who: Principal, job_id: str) -> tuple[dict[str, Any], TargetSchema]:
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        job = await repo.get_job(job_id)
        return job, await repo.get_schema(job["schema_id"], job["schema_version"])


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
async def get_job(request: Request, job_id: str, who: Auth) -> JobOut:
    """Status, progress, cost and stats."""
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        await repo.refresh_job_cost(job_id)
        job = await repo.get_job(job_id)
        schema = await repo.get_schema(job["schema_id"], job["schema_version"])
        return _job_out(
            job, await repo.job_document_ids(job_id), await repo.tasks(job_id), len(field_groups(schema))
        )


@router.get("/jobs/{job_id}/result", tags=["jobs"])
async def job_result(
    request: Request,
    job_id: str,
    who: Auth,
    format: OutputFormat | None = None,
    include_unreviewed: bool = False,
) -> Response:
    """The output file. Only approved values are written unless include_unreviewed=true. A job that is not `completed`
    returns a partial result with the header X-Formwork-Partial: true."""
    job, schema = await _job_bundle(request, who, job_id)
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        extractions = await repo.list_extractions(job_id)
        gaps = await repo.list_gaps(job_id)
    fmt = format or schema.output_format
    lineage = build_lineage(job_id, schema, extractions, gaps) if fmt is OutputFormat.XLSX else None
    body = render(schema, extractions, fmt, only_approved=not include_unreviewed, lineage=lineage)
    return Response(
        body,
        media_type=CONTENT_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}.{fmt.value}"',
            "X-Formwork-Partial": "false" if job["status"] == JobStatus.COMPLETED.value else "true",
            "X-Formwork-Job-Status": job["status"],
        },
    )


@router.get("/jobs/{job_id}/lineage", tags=["jobs"])
async def job_lineage(request: Request, job_id: str, who: Auth) -> dict[str, Any]:
    """Per-field evidence, confidence, extractor and alternatives, keyed by record and field."""
    _job, schema = await _job_bundle(request, who, job_id)
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        extractions = await repo.list_extractions(job_id)
        gaps = [
            {k: g[k] for k in ("id", "record_key", "field_path", "reason")}
            for g in await repo.list_gaps(job_id)
        ]
    return build_lineage(job_id, schema, extractions, gaps)


@router.get("/jobs/{job_id}/documents", tags=["jobs"])
async def job_documents(request: Request, job_id: str, who: Auth) -> list[dict[str, Any]]:
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        await repo.get_job(job_id)
        rows = [await repo.document_row(d) for d in await repo.job_document_ids(job_id)]
    return [
        {
            "id": r["id"],
            "file_id": r["file_id"],
            "parent_document_id": r["parent_document_id"],
            "filename": r["filename"],
            "mime_type": r["mime_type"],
            "page_count": r["page_count"],
            "doc_class": r["doc_class"],
            "digital": bool((r["meta"] or {}).get("digital")),
        }
        for r in rows
    ]


@router.get("/jobs/{job_id}/calls", tags=["jobs"])
async def job_calls(request: Request, job_id: str, who: Auth) -> dict[str, Any]:
    """Cost and latency per LLM call and per stage (the job page's cost breakdown)."""
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        await repo.get_job(job_id)
        calls = await repo.llm_calls(job_id)
        tasks = await repo.tasks(job_id)
    by_purpose: dict[str, dict[str, float]] = {}
    for c in calls:
        agg = by_purpose.setdefault(c["purpose"], {"calls": 0, "cost_usd": 0.0, "latency_ms": 0})
        agg["calls"] += 1
        agg["cost_usd"] = round(agg["cost_usd"] + float(c["cost_usd"] or 0), 6)
        agg["latency_ms"] += int(c["latency_ms"] or 0)
    stages: dict[str, int] = {}
    for t in tasks:
        stages[t["stage"]] = stages.get(t["stage"], 0) + int(t["duration_ms"] or 0)
    return {
        "calls": len(calls),
        "cost_usd": round(sum(float(c["cost_usd"] or 0) for c in calls), 6),
        "by_purpose": by_purpose,
        "stage_ms": stages,
    }


# ------------------------------------------------------------------ review
@router.get("/jobs/{job_id}/review", tags=["review"])
async def review_queue(request: Request, job_id: str, who: Auth, all: bool = False) -> dict[str, Any]:
    """Fields waiting for review (all=true returns every field, for the review grid)."""
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        job = await repo.get_job(job_id)
        items = await repo.list_extractions(job_id, None if all else REVIEW_STATUSES)
        gaps = await repo.list_gaps(job_id)
    waiting = sum(1 for e in items if e.status is not ExtractionStatus.APPROVED) + len(gaps)
    return {
        "job_id": job_id,
        "job_status": job["status"],
        "remaining": waiting,
        "items": [field_item(e) for e in items] + [gap_item(g) for g in gaps],
    }


@router.post("/jobs/{job_id}/review/{item_id}", response_model=ReviewResult, tags=["review"])
async def review_item(
    request: Request, job_id: str, item_id: str, body: ReviewAction, who: Auth
) -> ReviewResult:
    """Approve, correct or pick an alternative. Corrections are saved as eval cases."""
    s = fw(request)
    reviewer = (body.reviewer or f"key:{who.key_id}")[:120]
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        job = await repo.get_job(job_id, for_update=True)
        if job["status"] not in (
            JobStatus.NEEDS_REVIEW.value,
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
        ):
            raise ValidationFailed(f"job is {job['status']}; review opens when processing has finished")
        schema = await repo.get_schema(job["schema_id"], job["schema_version"])
        keep_text = ((await repo.tenant())["settings"] or {}).get("retention") != "zero"
        item, case = await apply_review(repo, schema, job_id, item_id, body, reviewer, keep_text)
    status = await s.runner.after_review(who.tenant_id, job_id)
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        repo = TenantRepo(conn, who.tenant_id)
        remaining = len(await repo.list_extractions(job_id, REVIEW_STATUSES)) + len(
            await repo.list_gaps(job_id)
        )
    return ReviewResult(item=item, job_status=status.value, remaining=remaining, eval_case_id=case)


# ------------------------------------------------------------------ documents (for the review UI)
@router.get("/documents/{document_id}", tags=["documents"])
async def get_document(request: Request, document_id: str, who: Auth) -> dict[str, Any]:
    async with tenant_tx(fw(request).engine, who.tenant_id) as conn:
        doc = await TenantRepo(conn, who.tenant_id).get_document(document_id)
    return doc.model_dump(mode="json", exclude={"blocks": {"__all__": {"embedding"}}})


@router.get("/documents/{document_id}/pages/{page}", tags=["documents"])
async def document_page(request: Request, document_id: str, page: int, who: Auth) -> Response:
    """Rendered page image (PNG). OCR boxes are relative to this image."""
    s = fw(request)
    async with tenant_tx(s.engine, who.tenant_id) as conn:
        await TenantRepo(conn, who.tenant_id).document_row(document_id)
    if page < 1:
        raise NotFound("page not found")
    data = await s.store.get(page_key(who.tenant_id, document_id, page))
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
