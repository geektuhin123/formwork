"""SQLAlchemy Core table definitions. The schema itself is created by Alembic (migrations/), not by metadata."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, MetaData, Numeric, Table, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import UserDefinedType

metadata = MetaData()


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect: Any) -> Any:
        return lambda v: None if v is None else "[" + ",".join(f"{x:.5f}" for x in v) + "]"

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        return lambda v: None if v is None else [float(x) for x in str(v).strip("[]").split(",") if x]


def _ts(name: str) -> Column[Any]:
    return Column(name, DateTime(timezone=True))


tenants = Table(
    "tenants",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text),
    Column("plan", Text),
    Column("webhook_secret", Text),
    Column("settings", JSONB),
    _ts("created_at"),
)
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("name", Text),
    Column("hash", Text),
    _ts("created_at"),
    _ts("revoked_at"),
    _ts("last_used_at"),
)
schemas = Table(
    "schemas",
    metadata,
    Column("id", Text, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("tenant_id", Text),
    Column("name", Text),
    Column("spec", JSONB),
    _ts("created_at"),
)
files = Table(
    "files",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("filename", Text),
    Column("mime_type", Text),
    Column("sha256", Text),
    Column("size_bytes", BigInteger),
    Column("storage_key", Text),
    _ts("created_at"),
)
documents = Table(
    "documents",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("file_id", Text),
    Column("parent_document_id", Text),
    Column("filename", Text),
    Column("mime_type", Text),
    Column("sha256", Text),
    Column("page_count", Integer),
    Column("doc_class", Text),
    Column("parser_version", Text),
    Column("meta", JSONB),
    _ts("created_at"),
)
blocks = Table(
    "blocks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("document_id", Text),
    Column("type", Text),
    Column("text", Text),
    Column("cells", JSONB),
    Column("bbox", JSONB),
    Column("locator", Text),
    Column("reading_order", Integer),
    Column("ocr_confidence", Float),
    Column("embedding", Vector(1024)),
)
jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("schema_id", Text),
    Column("schema_version", Integer),
    Column("status", Text),
    Column("options", JSONB),
    Column("file_ids", JSONB),
    Column("cost_usd", Numeric(10, 4)),
    Column("stats", JSONB),
    Column("error", Text),
    _ts("created_at"),
    _ts("finished_at"),
)
job_documents = Table(
    "job_documents",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("document_id", Text, primary_key=True),
    Column("tenant_id", Text),
)
job_tasks = Table(
    "job_tasks",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("stage", Text, primary_key=True),
    Column("document_id", Text, primary_key=True),
    Column("field_group", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("status", Text),
    Column("result", JSONB),
    Column("error", Text),
    Column("duration_ms", Integer),
    _ts("completed_at"),
)
extractions = Table(
    "extractions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("job_id", Text),
    Column("record_key", Text),
    Column("field_path", Text),
    Column("value", JSONB),
    Column("normalized_value", Text),
    Column("confidence", Float),
    Column("evidence", JSONB),
    Column("alternatives", JSONB),
    Column("extractor", Text),
    Column("attempt", Integer),
    Column("status", Text),
    Column("reason", Text),
    Column("signals", JSONB),
    Column("reviewed_by", Text),
    _ts("reviewed_at"),
)
review_gaps = Table(
    "review_gaps",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("job_id", Text),
    Column("record_key", Text),
    Column("field_path", Text),
    Column("reason", Text),
    _ts("resolved_at"),
)
mapping_plans = Table(
    "mapping_plans",
    metadata,
    Column("tenant_id", Text, primary_key=True),
    Column("schema_id", Text, primary_key=True),
    Column("schema_version", Integer, primary_key=True),
    Column("header_signature", Text, primary_key=True),
    Column("plan", JSONB),
)
llm_calls = Table(
    "llm_calls",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("job_id", Text),
    Column("model", Text),
    Column("purpose", Text),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_usd", Numeric(10, 6)),
    Column("latency_ms", Integer),
    Column("request_hash", Text),
    Column("response", JSONB),
    _ts("created_at"),
)
eval_cases = Table(
    "eval_cases",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("schema_id", Text),
    Column("field_path", Text),
    Column("block_id", Text),
    Column("expected", JSONB),
    Column("observed", JSONB),
    Column("source", Text),
    Column("context", JSONB),
    _ts("created_at"),
)
webhook_deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("id", Text, primary_key=True),
    Column("tenant_id", Text),
    Column("job_id", Text),
    Column("url", Text),
    Column("event", Text),
    Column("payload", JSONB),
    Column("status", Text),
    Column("attempts", Integer),
    Column("last_error", Text),
    _ts("created_at"),
    _ts("delivered_at"),
)

TENANT_TABLES = [
    "api_keys",
    "schemas",
    "files",
    "documents",
    "blocks",
    "jobs",
    "job_documents",
    "job_tasks",
    "extractions",
    "review_gaps",
    "mapping_plans",
    "llm_calls",
    "eval_cases",
    "webhook_deliveries",
]
