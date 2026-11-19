"""The six contract models shared by every Formwork module (see docs/lld.md, "Core data models").

Additions over the LLD listing are optional fields only and are recorded in docs/adr/0001-core-model-additions.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

Scalar = str | float | int | bool | None


# ---------- Target side ----------
class FieldType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    CURRENCY = "currency"
    OBJECT = "object"
    ARRAY = "array"


class OutputFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


class FieldSpec(BaseModel):
    path: str  # "invoice.line_items[].amount"
    type: FieldType
    description: str  # what to look for, in plain words
    required: bool = True
    enum_values: list[str] | None = None
    format: str | None = None  # "YYYY-MM-DD", ISO 4217, regex
    examples: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)  # "amount == qty * unit_price"
    merge_policy: str = "prefer_highest_confidence"
    children: list[FieldSpec] = Field(default_factory=list)

    @property
    def name(self) -> str:
        """Last path segment without array markers: 'line_items[].amount' -> 'amount'."""
        return self.path.split(".")[-1].replace("[]", "")

    @model_validator(mode="after")
    def _check(self) -> FieldSpec:
        if not self.path.strip():
            raise ValueError("FieldSpec.path must not be empty")
        if self.type is FieldType.ENUM and not self.enum_values:
            raise ValueError(f"enum field {self.path!r} needs enum_values")
        if self.type in (FieldType.ARRAY, FieldType.OBJECT) and not self.children:
            raise ValueError(f"{self.type.value} field {self.path!r} needs children")
        return self


class TargetSchema(BaseModel):
    id: str
    tenant_id: str
    name: str
    version: int
    record_grain: str  # "one row per invoice line"
    entity_key: list[str]  # fields that identify one record across sources
    fields: list[FieldSpec]
    output_format: OutputFormat = OutputFormat.JSON
    source_hint: str | None = None  # json_schema | sample | prompt

    def leaf_fields(self) -> list[FieldSpec]:
        out: list[FieldSpec] = []

        def walk(fs: list[FieldSpec]) -> None:
            for f in fs:
                if f.children:
                    walk(f.children)
                else:
                    out.append(f)

        walk(self.fields)
        return out

    def field(self, path: str) -> FieldSpec | None:
        """Look up a spec by spec path; concrete indexes ('items[3].qty') are accepted."""
        from formwork_core.paths import spec_path

        want = spec_path(path)
        stack = list(self.fields)
        while stack:
            f = stack.pop()
            if f.path == want:
                return f
            stack.extend(f.children)
        return None

    @model_validator(mode="after")
    def _check(self) -> TargetSchema:
        if not self.fields:
            raise ValueError("TargetSchema needs at least one field")
        paths = [f.path for f in self.leaf_fields()]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate field paths in schema")
        return self


# ---------- Source side ----------
class BBox(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float  # normalised 0..1

    @model_validator(mode="after")
    def _check(self) -> BBox:
        for v in (self.x0, self.y0, self.x1, self.y1):
            if not -0.001 <= v <= 1.001:
                raise ValueError("bbox coordinates must be normalised to 0..1")
        if self.page < 1:
            raise ValueError("bbox.page is 1-based")
        return self


class BlockType(StrEnum):
    TITLE = "title"
    TEXT = "text"
    TABLE = "table"
    KEY_VALUE = "key_value"
    FIGURE = "figure"
    HANDWRITING = "handwriting"
    CHECKBOX = "checkbox"
    SHEET_RANGE = "sheet_range"


class Block(BaseModel):
    id: str
    document_id: str
    type: BlockType
    text: str  # markdown; tables as GitHub tables
    cells: list[list[str]] | None = None
    bbox: BBox | None = None  # None for sheets/APIs; use locator instead
    locator: str | None = None  # "Sheet1!A2:F40", "email.body", "$.orders[3]"
    reading_order: int
    ocr_confidence: float | None = None
    embedding: list[float] | None = None

    @model_validator(mode="after")
    def _check(self) -> Block:
        if self.bbox is None and not self.locator:
            raise ValueError("Block needs a bbox or a locator")
        return self


class CanonicalDocument(BaseModel):
    id: str
    job_id: str
    filename: str
    mime_type: str
    sha256: str
    page_count: int
    doc_class: str | None = None  # "invoice", "bank_statement"
    blocks: list[Block]

    def block(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)


# ---------- Result side ----------
class Evidence(BaseModel):
    block_id: str
    document_id: str
    bbox: BBox | None = None
    locator: str | None = None
    quote: str  # exact span the value came from

    @model_validator(mode="after")
    def _check(self) -> Evidence:
        if self.bbox is None and not self.locator:
            raise ValueError("Evidence needs a bbox or a locator")
        if not self.block_id or not self.quote.strip():
            raise ValueError("Evidence needs a block_id and a non-empty quote")
        return self


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    CONFLICT = "conflict"
    FAILED_VALIDATION = "failed_validation"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class Alternative(BaseModel):
    """A losing value kept beside the winner so a reviewer can flip the choice."""

    value: Scalar
    normalized_value: str | None = None
    confidence: float
    extractor: str
    evidence: list[Evidence] = Field(min_length=1)


class Extraction(BaseModel):
    record_key: str  # readable slug (or hash) of the normalised entity_key values
    field_path: str  # concrete path: "line_items[2].amount"
    value: Scalar
    normalized_value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)  # 0..1, calibrated
    evidence: list[Evidence]
    extractor: str  # "tabular", "llm:haiku", "vlm:sonnet"
    attempt: int = 1
    status: ExtractionStatus = ExtractionStatus.EXTRACTED
    # --- additions (ADR 0001) ---
    id: str | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    reason: str | None = None  # why it failed validation / went to review
    signals: dict[str, float] = Field(default_factory=dict)  # confidence inputs

    @model_validator(mode="after")
    def _check(self) -> Extraction:
        if not self.evidence:
            raise ValueError("Extraction without evidence is rejected: lineage is not optional")
        return self


class JobStatus(StrEnum):
    QUEUED = "queued"
    INGESTING = "ingesting"
    EXTRACTING = "extracting"
    MERGING = "merging"
    VALIDATING = "validating"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED)


class Job(BaseModel):
    id: str
    tenant_id: str
    schema_id: str
    schema_version: int
    status: JobStatus
    document_ids: list[str]
    cost_usd: float = 0
    budget_usd: float | None = None
    stats: dict[str, Any] = Field(default_factory=dict)  # fields total / auto-approved / in review
