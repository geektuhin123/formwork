"""Request/response models of the HTTP API (the OpenAPI spec is generated from these)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from formwork_core.models import FieldSpec, OutputFormat, Scalar, TargetSchema
from pydantic import BaseModel, Field, model_validator


class CompileRequest(BaseModel):
    source: Literal["json_schema", "prompt"]
    json_schema: dict[str, Any] | None = None
    prompt: str | None = Field(default=None, max_length=8000)
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> CompileRequest:
        if self.source == "json_schema" and not self.json_schema:
            raise ValueError("json_schema is required when source is 'json_schema'")
        if self.source == "prompt" and not (self.prompt or "").strip():
            raise ValueError("prompt is required when source is 'prompt'")
        return self


class Warning_(BaseModel):
    path: str
    words: int
    message: str


class CompileResponse(BaseModel):
    schema_: TargetSchema = Field(alias="schema")
    warnings: list[Warning_]

    model_config = {"populate_by_name": True}


class SchemaIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    record_grain: str = Field(min_length=1, max_length=300)
    entity_key: list[str] = Field(min_length=1, max_length=8)
    fields: list[FieldSpec] = Field(min_length=1, max_length=300)
    output_format: OutputFormat = OutputFormat.JSON
    source_hint: str | None = None


class SchemaSummary(BaseModel):
    id: str
    version: int
    name: str
    field_count: int
    weak_descriptions: int
    created_at: datetime | None


class FileOut(BaseModel):
    file_id: str
    filename: str
    mime_type: str
    sha256: str
    size_bytes: int
    deduplicated: bool


class JobOptions(BaseModel):
    auto_approve_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    budget_usd: float | None = Field(default=None, gt=0)
    source_priority: list[str] = Field(default_factory=list, max_length=50)
    webhook_url: str | None = Field(default=None, max_length=2000)
    repair: bool = True


class JobCreate(BaseModel):
    schema_id: str
    schema_version: int | None = None
    file_ids: list[str] = Field(min_length=1)
    options: JobOptions = Field(default_factory=JobOptions)


class JobOut(BaseModel):
    id: str
    status: str
    schema_id: str
    schema_version: int
    file_ids: list[str]
    document_ids: list[str] = Field(default_factory=list)
    cost_usd: float
    budget_usd: float | None
    progress: dict[str, Any]
    stats: dict[str, Any]
    options: dict[str, Any]
    error: str | None
    created_at: datetime | None
    finished_at: datetime | None


class ReviewAction(BaseModel):
    action: Literal["approve", "correct", "pick_alternative"]
    value: Scalar = None
    alternative_index: int | None = Field(default=None, ge=0)
    reviewer: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check(self) -> ReviewAction:
        if self.action == "correct" and (
            self.value is None or (isinstance(self.value, str) and not self.value.strip())
        ):
            raise ValueError("value is required for action 'correct'")
        if self.action == "pick_alternative" and self.alternative_index is None:
            raise ValueError("alternative_index is required for action 'pick_alternative'")
        return self


class ReviewResult(BaseModel):
    item: dict[str, Any]
    job_status: str
    remaining: int
    eval_case_id: str | None = None
