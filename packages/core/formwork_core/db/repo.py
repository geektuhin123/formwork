"""Tenant-scoped repository. Every statement filters on tenant_id (and RLS enforces it again underneath)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from formwork_core.db import tables as t
from formwork_core.db.session import tenant_tx
from formwork_core.errors import NotFound
from formwork_core.ids import new_id
from formwork_core.models import (
    Block,
    CanonicalDocument,
    Extraction,
    ExtractionStatus,
    JobStatus,
    TargetSchema,
)
from formwork_core.provider import LLMCallRecord

Row = dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


class TenantRepo:
    def __init__(self, conn: AsyncConnection, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("TenantRepo requires a tenant_id")
        self.conn = conn
        self.tenant_id = tenant_id

    async def _one(self, stmt: Any) -> Row | None:
        row = (await self.conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def _all(self, stmt: Any) -> list[Row]:
        return [dict(r) for r in (await self.conn.execute(stmt)).mappings().all()]

    # ---------------------------------------------------------------- tenant
    async def tenant(self) -> Row:
        row = await self._one(select(t.tenants).where(t.tenants.c.id == self.tenant_id))
        if row is None:
            raise NotFound("tenant not found")
        return row

    # ---------------------------------------------------------------- schemas
    async def save_schema(self, schema: TargetSchema) -> TargetSchema:
        latest = (
            await self.conn.execute(
                select(func.max(t.schemas.c.version)).where(
                    and_(t.schemas.c.tenant_id == self.tenant_id, t.schemas.c.id == schema.id)
                )
            )
        ).scalar()
        saved = schema.model_copy(update={"version": (latest or 0) + 1, "tenant_id": self.tenant_id})
        await self.conn.execute(
            insert(t.schemas).values(
                id=saved.id,
                version=saved.version,
                tenant_id=self.tenant_id,
                name=saved.name,
                spec=saved.model_dump(mode="json"),
                created_at=_now(),
            )
        )
        return saved

    async def get_schema(self, schema_id: str, version: int | None = None) -> TargetSchema:
        stmt = select(t.schemas.c.spec).where(
            and_(t.schemas.c.tenant_id == self.tenant_id, t.schemas.c.id == schema_id)
        )
        stmt = (
            stmt.where(t.schemas.c.version == version)
            if version
            else stmt.order_by(t.schemas.c.version.desc())
        )
        row = await self._one(stmt.limit(1))
        if row is None:
            raise NotFound(f"schema {schema_id} not found")
        return TargetSchema.model_validate(row["spec"])

    async def list_schemas(self) -> list[Row]:
        s = t.schemas
        latest = (
            select(s.c.id, func.max(s.c.version).label("version"))
            .where(s.c.tenant_id == self.tenant_id)
            .group_by(s.c.id)
        ).subquery()
        return await self._all(
            select(s.c.id, s.c.version, s.c.name, s.c.created_at, s.c.spec)
            .join(latest, and_(s.c.id == latest.c.id, s.c.version == latest.c.version))
            .where(s.c.tenant_id == self.tenant_id)
            .order_by(s.c.created_at.desc())
        )

    # ---------------------------------------------------------------- files
    async def upsert_file(
        self, filename: str, mime_type: str, sha256: str, size: int, storage_key: str
    ) -> tuple[Row, bool]:
        file_id = new_id("fil")
        res = await self.conn.execute(
            pg_insert(t.files)
            .values(
                id=file_id,
                tenant_id=self.tenant_id,
                filename=filename,
                mime_type=mime_type,
                sha256=sha256,
                size_bytes=size,
                storage_key=storage_key,
                created_at=_now(),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "sha256"])
            .returning(t.files.c.id)
        )
        created = res.first() is not None
        row = await self._one(
            select(t.files).where(and_(t.files.c.tenant_id == self.tenant_id, t.files.c.sha256 == sha256))
        )
        assert row is not None
        return row, created

    async def get_file(self, file_id: str) -> Row:
        row = await self._one(
            select(t.files).where(and_(t.files.c.tenant_id == self.tenant_id, t.files.c.id == file_id))
        )
        if row is None:
            raise NotFound(f"file {file_id} not found")
        return row

    # ---------------------------------------------------------------- documents
    async def save_document(
        self,
        doc: CanonicalDocument,
        file_id: str,
        parent_document_id: str | None,
        parser_version: str,
        meta: Row,
    ) -> None:
        await self.conn.execute(
            pg_insert(t.documents)
            .values(
                id=doc.id,
                tenant_id=self.tenant_id,
                file_id=file_id,
                parent_document_id=parent_document_id,
                filename=doc.filename,
                mime_type=doc.mime_type,
                sha256=doc.sha256,
                page_count=doc.page_count,
                doc_class=doc.doc_class,
                parser_version=parser_version,
                meta=meta,
                created_at=_now(),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        rows = [
            {
                "id": b.id,
                "tenant_id": self.tenant_id,
                "document_id": doc.id,
                "type": b.type.value,
                "text": b.text,
                "cells": b.cells,
                "bbox": b.bbox.model_dump() if b.bbox else None,
                "locator": b.locator,
                "reading_order": b.reading_order,
                "ocr_confidence": b.ocr_confidence,
                "embedding": b.embedding,
            }
            for b in doc.blocks
        ]
        for i in range(0, len(rows), 200):
            await self.conn.execute(
                pg_insert(t.blocks).on_conflict_do_nothing(index_elements=["id"]), rows[i : i + 200]
            )

    async def document_row(self, document_id: str) -> Row:
        row = await self._one(
            select(t.documents).where(
                and_(t.documents.c.tenant_id == self.tenant_id, t.documents.c.id == document_id)
            )
        )
        if row is None:
            raise NotFound(f"document {document_id} not found")
        return row

    async def get_document(
        self, document_id: str, job_id: str = "", with_embeddings: bool = False
    ) -> CanonicalDocument:
        row = await self.document_row(document_id)
        b = t.blocks
        cols = [c for c in b.c if with_embeddings or c.name != "embedding"]
        brows = await self._all(
            select(*cols)
            .where(and_(b.c.tenant_id == self.tenant_id, b.c.document_id == document_id))
            .order_by(b.c.reading_order)
        )
        blocks = [Block.model_validate({k: v for k, v in r.items() if k != "tenant_id"}) for r in brows]
        return CanonicalDocument(
            id=row["id"],
            job_id=job_id,
            filename=row["filename"] or "",
            mime_type=row["mime_type"] or "",
            sha256=row["sha256"] or "",
            page_count=row["page_count"] or 0,
            doc_class=row["doc_class"],
            blocks=blocks,
        )

    async def documents_for_file(self, file_id: str) -> list[Row]:
        d = t.documents
        return await self._all(
            select(d)
            .where(and_(d.c.tenant_id == self.tenant_id, d.c.file_id == file_id))
            .order_by(d.c.created_at, d.c.id)
        )

    async def set_doc_class(self, document_id: str, doc_class: str) -> None:
        d = t.documents
        await self.conn.execute(
            update(d)
            .where(and_(d.c.tenant_id == self.tenant_id, d.c.id == document_id))
            .values(doc_class=doc_class)
        )

    # ---------------------------------------------------------------- jobs
    async def create_job(self, schema_id: str, schema_version: int, file_ids: list[str], options: Row) -> Row:
        job_id = new_id("job")
        await self.conn.execute(
            insert(t.jobs).values(
                id=job_id,
                tenant_id=self.tenant_id,
                schema_id=schema_id,
                schema_version=schema_version,
                status=JobStatus.QUEUED.value,
                options=options,
                file_ids=file_ids,
                cost_usd=0,
                stats={},
                created_at=_now(),
            )
        )
        return await self.get_job(job_id)

    async def get_job(self, job_id: str, for_update: bool = False) -> Row:
        stmt = select(t.jobs).where(and_(t.jobs.c.tenant_id == self.tenant_id, t.jobs.c.id == job_id))
        row = await self._one(stmt.with_for_update() if for_update else stmt)
        if row is None:
            raise NotFound(f"job {job_id} not found")
        row["cost_usd"] = float(row["cost_usd"] or 0)
        return row

    async def list_jobs(self, limit: int = 50) -> list[Row]:
        rows = await self._all(
            select(t.jobs)
            .where(t.jobs.c.tenant_id == self.tenant_id)
            .order_by(t.jobs.c.created_at.desc())
            .limit(limit)
        )
        for r in rows:
            r["cost_usd"] = float(r["cost_usd"] or 0)
        return rows

    async def set_job_status(
        self, job_id: str, status: JobStatus, error: str | None = None, stats: Row | None = None
    ) -> bool:
        """Returns True when the status actually changed (callers fire a webhook on change)."""
        job = await self.get_job(job_id, for_update=True)
        values: Row = {"status": status.value}
        if error is not None:
            values["error"] = error[:2000]
        if stats is not None:
            values["stats"] = {**(job["stats"] or {}), **stats}
        if status.terminal or status is JobStatus.NEEDS_REVIEW:
            values["finished_at"] = _now()
        await self.conn.execute(
            update(t.jobs)
            .where(and_(t.jobs.c.tenant_id == self.tenant_id, t.jobs.c.id == job_id))
            .values(**values)
        )
        return bool(job["status"] != status.value)

    async def refresh_job_cost(self, job_id: str) -> float:
        lc = t.llm_calls
        total = (
            await self.conn.execute(
                select(func.coalesce(func.sum(lc.c.cost_usd), 0)).where(
                    and_(lc.c.tenant_id == self.tenant_id, lc.c.job_id == job_id)
                )
            )
        ).scalar()
        cost = float(total or 0)
        await self.conn.execute(
            update(t.jobs)
            .where(and_(t.jobs.c.tenant_id == self.tenant_id, t.jobs.c.id == job_id))
            .values(cost_usd=cost)
        )
        return cost

    async def link_documents(self, job_id: str, document_ids: list[str]) -> None:
        if document_ids:
            await self.conn.execute(
                pg_insert(t.job_documents).on_conflict_do_nothing(),
                [{"job_id": job_id, "document_id": d, "tenant_id": self.tenant_id} for d in document_ids],
            )

    async def job_document_ids(self, job_id: str) -> list[str]:
        jd = t.job_documents
        rows = await self._all(
            select(jd.c.document_id)
            .where(and_(jd.c.tenant_id == self.tenant_id, jd.c.job_id == job_id))
            .order_by(jd.c.document_id)
        )
        return [r["document_id"] for r in rows]

    # ---------------------------------------------------------------- stage tasks (idempotency keys)
    async def task(self, job_id: str, stage: str, document_id: str = "", field_group: str = "") -> Row | None:
        jt = t.job_tasks
        return await self._one(
            select(jt).where(
                and_(
                    jt.c.tenant_id == self.tenant_id,
                    jt.c.job_id == job_id,
                    jt.c.stage == stage,
                    jt.c.document_id == document_id,
                    jt.c.field_group == field_group,
                )
            )
        )

    async def complete_task(
        self,
        job_id: str,
        stage: str,
        document_id: str = "",
        field_group: str = "",
        result: Any = None,
        duration_ms: int = 0,
        status: str = "done",
        error: str | None = None,
    ) -> None:
        values = {
            "job_id": job_id,
            "stage": stage,
            "document_id": document_id,
            "field_group": field_group,
            "tenant_id": self.tenant_id,
            "status": status,
            "result": result,
            "duration_ms": duration_ms,
            "error": error,
            "completed_at": _now(),
        }
        stmt = pg_insert(t.job_tasks).values(**values)
        await self.conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["job_id", "stage", "document_id", "field_group"],
                set_={k: values[k] for k in ("status", "result", "duration_ms", "error", "completed_at")},
            )
        )

    async def tasks(self, job_id: str, stage: str | None = None) -> list[Row]:
        jt = t.job_tasks
        stmt = select(jt).where(and_(jt.c.tenant_id == self.tenant_id, jt.c.job_id == job_id))
        if stage:
            stmt = stmt.where(jt.c.stage == stage)
        return await self._all(stmt.order_by(jt.c.completed_at))

    async def clear_tasks(self, job_id: str, stages: list[str]) -> None:
        jt = t.job_tasks
        await self.conn.execute(
            delete(jt).where(
                and_(jt.c.tenant_id == self.tenant_id, jt.c.job_id == job_id, jt.c.stage.in_(stages))
            )
        )

    # ---------------------------------------------------------------- extractions
    def _ext_row(self, job_id: str, e: Extraction) -> Row:
        return {
            "id": e.id or new_id("ext"),
            "tenant_id": self.tenant_id,
            "job_id": job_id,
            "record_key": e.record_key,
            "field_path": e.field_path,
            "value": e.value,
            "normalized_value": e.normalized_value,
            "confidence": e.confidence,
            "evidence": [ev.model_dump() for ev in e.evidence],
            "alternatives": [a.model_dump() for a in e.alternatives],
            "extractor": e.extractor,
            "attempt": e.attempt,
            "status": e.status.value,
            "reason": e.reason,
            "signals": e.signals,
        }

    async def replace_extractions(self, job_id: str, items: list[Extraction]) -> None:
        ex = t.extractions
        await self.conn.execute(
            delete(ex).where(and_(ex.c.tenant_id == self.tenant_id, ex.c.job_id == job_id))
        )
        rows = [self._ext_row(job_id, e) for e in items]
        for i in range(0, len(rows), 500):
            await self.conn.execute(insert(ex), rows[i : i + 500])

    async def insert_extraction(self, job_id: str, e: Extraction) -> str:
        row = self._ext_row(job_id, e)
        await self.conn.execute(insert(t.extractions).values(**row))
        return str(row["id"])

    @staticmethod
    def _to_extraction(r: Row) -> Extraction:
        return Extraction.model_validate(
            {
                k: r[k]
                for k in (
                    "id",
                    "record_key",
                    "field_path",
                    "value",
                    "normalized_value",
                    "confidence",
                    "evidence",
                    "alternatives",
                    "extractor",
                    "attempt",
                    "status",
                    "reason",
                    "signals",
                )
            }
        )

    async def list_extractions(
        self, job_id: str, statuses: list[ExtractionStatus] | None = None
    ) -> list[Extraction]:
        ex = t.extractions
        stmt = select(ex).where(and_(ex.c.tenant_id == self.tenant_id, ex.c.job_id == job_id))
        if statuses:
            stmt = stmt.where(ex.c.status.in_([s.value for s in statuses]))
        return [
            self._to_extraction(r) for r in await self._all(stmt.order_by(ex.c.record_key, ex.c.field_path))
        ]

    async def get_extraction(self, job_id: str, extraction_id: str) -> Extraction:
        ex = t.extractions
        row = await self._one(
            select(ex)
            .where(and_(ex.c.tenant_id == self.tenant_id, ex.c.job_id == job_id, ex.c.id == extraction_id))
            .with_for_update()
        )
        if row is None:
            raise NotFound(f"extraction {extraction_id} not found")
        return self._to_extraction(row)

    async def update_extraction(self, job_id: str, e: Extraction, reviewed_by: str | None = None) -> None:
        ex = t.extractions
        row = self._ext_row(job_id, e)
        values = {
            k: row[k]
            for k in (
                "value",
                "normalized_value",
                "confidence",
                "evidence",
                "alternatives",
                "extractor",
                "attempt",
                "status",
                "reason",
                "signals",
            )
        }
        if reviewed_by:
            values.update(reviewed_by=reviewed_by, reviewed_at=_now())
        await self.conn.execute(
            update(ex)
            .where(and_(ex.c.tenant_id == self.tenant_id, ex.c.job_id == job_id, ex.c.id == e.id))
            .values(**values)
        )

    # ---------------------------------------------------------------- review gaps (required fields with no value)
    async def replace_gaps(self, job_id: str, gaps: list[Row]) -> None:
        g = t.review_gaps
        await self.conn.execute(delete(g).where(and_(g.c.tenant_id == self.tenant_id, g.c.job_id == job_id)))
        if gaps:
            await self.conn.execute(
                insert(g),
                [
                    {
                        "id": new_id("gap"),
                        "tenant_id": self.tenant_id,
                        "job_id": job_id,
                        "record_key": x["record_key"],
                        "field_path": x["field_path"],
                        "reason": x.get("reason"),
                    }
                    for x in gaps
                ],
            )

    async def list_gaps(self, job_id: str, open_only: bool = True) -> list[Row]:
        g = t.review_gaps
        stmt = select(g).where(and_(g.c.tenant_id == self.tenant_id, g.c.job_id == job_id))
        if open_only:
            stmt = stmt.where(g.c.resolved_at.is_(None))
        return await self._all(stmt.order_by(g.c.record_key, g.c.field_path))

    async def resolve_gap(self, job_id: str, gap_id: str) -> Row:
        g = t.review_gaps
        row = await self._one(
            select(g).where(and_(g.c.tenant_id == self.tenant_id, g.c.job_id == job_id, g.c.id == gap_id))
        )
        if row is None:
            raise NotFound(f"gap {gap_id} not found")
        await self.conn.execute(
            update(g)
            .where(and_(g.c.tenant_id == self.tenant_id, g.c.id == gap_id))
            .values(resolved_at=_now())
        )
        return row

    # ---------------------------------------------------------------- mapping plans
    async def get_plan(self, schema_id: str, schema_version: int, signature: str) -> Row | None:
        m = t.mapping_plans
        row = await self._one(
            select(m.c.plan).where(
                and_(
                    m.c.tenant_id == self.tenant_id,
                    m.c.schema_id == schema_id,
                    m.c.schema_version == schema_version,
                    m.c.header_signature == signature,
                )
            )
        )
        return row["plan"] if row else None

    async def put_plan(self, schema_id: str, schema_version: int, signature: str, plan: Row) -> None:
        await self.conn.execute(
            pg_insert(t.mapping_plans)
            .values(
                tenant_id=self.tenant_id,
                schema_id=schema_id,
                schema_version=schema_version,
                header_signature=signature,
                plan=plan,
            )
            .on_conflict_do_nothing()
        )

    # ---------------------------------------------------------------- eval cases
    async def add_eval_case(
        self,
        schema_id: str,
        field_path: str,
        block_id: str,
        expected: Any,
        observed: Any,
        source: str,
        context: Row,
    ) -> str:
        case_id = new_id("evc")
        await self.conn.execute(
            insert(t.eval_cases).values(
                id=case_id,
                tenant_id=self.tenant_id,
                schema_id=schema_id,
                field_path=field_path,
                block_id=block_id,
                expected=expected,
                observed=observed,
                source=source,
                context=context,
                created_at=_now(),
            )
        )
        return case_id

    async def list_eval_cases(self, schema_id: str | None = None) -> list[Row]:
        ec = t.eval_cases
        stmt = select(ec).where(ec.c.tenant_id == self.tenant_id)
        if schema_id:
            stmt = stmt.where(ec.c.schema_id == schema_id)
        return await self._all(stmt.order_by(ec.c.created_at))

    # ---------------------------------------------------------------- llm calls
    async def llm_calls(self, job_id: str) -> list[Row]:
        lc = t.llm_calls
        cols = [c for c in lc.c if c.name != "response"]
        return await self._all(
            select(*cols)
            .where(and_(lc.c.tenant_id == self.tenant_id, lc.c.job_id == job_id))
            .order_by(lc.c.created_at)
        )

    # ---------------------------------------------------------------- webhooks
    async def add_delivery(self, job_id: str, url: str, event: str, payload: Row) -> str:
        delivery_id = new_id("whd")
        await self.conn.execute(
            insert(t.webhook_deliveries).values(
                id=delivery_id,
                tenant_id=self.tenant_id,
                job_id=job_id,
                url=url,
                event=event,
                payload=payload,
                status="pending",
                attempts=0,
                created_at=_now(),
            )
        )
        return delivery_id

    async def get_delivery(self, delivery_id: str) -> Row:
        w = t.webhook_deliveries
        row = await self._one(select(w).where(and_(w.c.tenant_id == self.tenant_id, w.c.id == delivery_id)))
        if row is None:
            raise NotFound("delivery not found")
        return row

    async def mark_delivery(self, delivery_id: str, status: str, attempts: int, error: str | None) -> None:
        w = t.webhook_deliveries
        values: Row = {"status": status, "attempts": attempts, "last_error": error}
        if status == "delivered":
            values["delivered_at"] = _now()
        await self.conn.execute(
            update(w).where(and_(w.c.tenant_id == self.tenant_id, w.c.id == delivery_id)).values(**values)
        )

    # ---------------------------------------------------------------- retention
    async def purge_file_content(self, file_ids: list[str]) -> None:
        """Zero-retention: drop block text/cells for these files; extractions and lineage quotes stay."""
        d, b = t.documents, t.blocks
        docs = select(d.c.id).where(and_(d.c.tenant_id == self.tenant_id, d.c.file_id.in_(file_ids)))
        await self.conn.execute(
            update(b)
            .where(and_(b.c.tenant_id == self.tenant_id, b.c.document_id.in_(docs)))
            .values(text="", cells=None, embedding=None)
        )


class DbCallSink:
    """Writes llm_calls in its own short transaction so cost is recorded even if the calling stage later fails."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def record(self, rec: LLMCallRecord) -> None:
        async with tenant_tx(self.engine, rec.tenant_id) as conn:
            await conn.execute(pg_insert(t.llm_calls).values(**rec.model_dump()).on_conflict_do_nothing())

    async def lookup(self, tenant_id: str, job_id: str | None, request_hash: str) -> dict[str, Any] | None:
        if job_id is None:
            return None
        lc = t.llm_calls
        async with tenant_tx(self.engine, tenant_id) as conn:
            row = (
                await conn.execute(
                    select(lc.c.response).where(
                        and_(
                            lc.c.tenant_id == tenant_id,
                            lc.c.job_id == job_id,
                            lc.c.request_hash == request_hash,
                        )
                    )
                )
            ).first()
        return row[0] if row and row[0] is not None else None


# ---------------------------------------------------------------- cross-tenant entry points (SECURITY DEFINER functions)
async def lookup_api_key(engine: AsyncEngine, key_hash: str) -> tuple[str, str] | None:
    async with engine.begin() as conn:
        row = (
            await conn.execute(text("select key_id, tenant_id from fw_lookup_api_key(:h)"), {"h": key_hash})
        ).first()
    return (row[0], row[1]) if row else None


async def active_jobs(engine: AsyncEngine) -> list[tuple[str, str]]:
    async with engine.begin() as conn:
        rows = (await conn.execute(text("select tenant_id, job_id from fw_active_jobs()"))).all()
    return [(r[0], r[1]) for r in rows]


async def pending_webhooks(engine: AsyncEngine) -> list[tuple[str, str]]:
    async with engine.begin() as conn:
        rows = (await conn.execute(text("select tenant_id, delivery_id from fw_pending_webhooks()"))).all()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------- admin (owner connection only)
async def create_tenant(
    admin: AsyncEngine, name: str, plan: str = "trial", settings: Row | None = None
) -> Row:
    from formwork_core.security import generate_webhook_secret

    tenant_id = new_id("ten")
    secret = generate_webhook_secret()
    async with admin.begin() as conn:
        await conn.execute(
            insert(t.tenants).values(
                id=tenant_id,
                name=name,
                plan=plan,
                webhook_secret=secret,
                settings=settings or {},
                created_at=_now(),
            )
        )
    return {"id": tenant_id, "name": name, "webhook_secret": secret}


async def create_api_key(
    admin: AsyncEngine, tenant_id: str, pepper: str, name: str = "default", env: str = "live"
) -> str:
    from formwork_core.security import generate_api_key, hash_api_key

    key = generate_api_key(env)
    async with admin.begin() as conn:
        await conn.execute(
            insert(t.api_keys).values(
                id=new_id("key"),
                tenant_id=tenant_id,
                name=name,
                hash=hash_api_key(key, pepper),
                created_at=_now(),
            )
        )
    return key


async def revoke_api_key(admin: AsyncEngine, key_id: str) -> None:
    async with admin.begin() as conn:
        await conn.execute(update(t.api_keys).where(t.api_keys.c.id == key_id).values(revoked_at=_now()))
