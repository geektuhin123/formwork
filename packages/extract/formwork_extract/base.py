"""LLM extractors: one tool-use call per (field group, block set). Every value comes back as
{value, block_id, quote, self_confidence} and must survive the grounding check before it becomes an Extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from formwork_core.context import JobContext
from formwork_core.errors import NotFound
from formwork_core.groups import FieldGroup
from formwork_core.logging import get_logger
from formwork_core.models import Block, BlockType, Evidence, Extraction, ExtractionStatus, FieldSpec, Scalar
from formwork_core.normalize import normalize, typed
from formwork_core.paths import concrete_path
from formwork_core.prompting import UNTRUSTED_NOTICE, render_block
from formwork_core.provider import LLMRequest, ModelTier, ToolSpec
from formwork_core.storage import crop_key
from formwork_core.telemetry import span

from formwork_extract.confidence import GROUNDING, MODEL, OCR, blend
from formwork_extract.grounding import GROUNDING_MIN, quote_match, value_supported
from formwork_extract.injection import mask, overlaps

log = get_logger("formwork.extract")
TABLE_PAGE_ROWS = 40
MAX_IMAGES = 8
PROVISIONAL_PREFIX = "doc:"

_VALUE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["value", "block_id", "quote", "self_confidence"],
    "properties": {
        "value": {
            "type": ["string", "number", "boolean", "null"],
            "description": "The value exactly as the document states it.",
        },
        "block_id": {"type": "string", "description": "id of the <block> the value was read from."},
        "quote": {
            "type": "string",
            "description": "Text copied character-for-character from that block that states the value.",
        },
        "self_confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}
_RULES = (
    "You extract structured fields from business documents.\n"
    "Rules:\n"
    "- Use only the text inside the <block> tags. If a field is not stated, return null for it. Never guess or compute.\n"
    "- For every value give the block_id and a quote copied exactly from that block; the quote must contain the value.\n"
    "- Keep numbers and dates as written in the document; do not reformat or convert.\n"
    "- For repeating rows return one object per row, each quoting its own row.\n"
    f"- {UNTRUSTED_NOTICE}\n"
)


@dataclass
class RejectedValue:
    """A value the model returned that failed grounding. It never becomes an Extraction and never reaches output."""

    field_path: str
    document_id: str
    value: Scalar
    quote: str
    reason: str  # ungrounded | value_not_in_quote | unknown_block | untrusted_instruction


@dataclass
class ExtractionReport:
    extractions: list[Extraction] = field(default_factory=list)
    rejected: list[RejectedValue] = field(default_factory=list)

    def extend(self, other: ExtractionReport) -> None:
        self.extractions.extend(other.extractions)
        self.rejected.extend(other.rejected)


class Extractor(Protocol):
    name: str

    async def extract(self, group: FieldGroup, blocks: list[Block], ctx: JobContext) -> list[Extraction]: ...


def field_listing(group: FieldGroup) -> list[dict[str, Any]]:
    out = []
    for f in group.fields:
        item: dict[str, Any] = {
            "key": group.key(f),
            "path": f.path,
            "type": f.type.value,
            "description": f.description,
        }
        if f.enum_values:
            item["enum_values"] = f.enum_values
        if f.format:
            item["format"] = f.format
        if f.examples:
            item["examples"] = f.examples[:3]
        out.append(item)
    return out


def tool_for(group: FieldGroup) -> ToolSpec:
    props = {
        group.key(f): (
            {**_VALUE_SCHEMA, "description": f.description}
            if group.is_array
            else {"anyOf": [_VALUE_SCHEMA, {"type": "null"}], "description": f.description}
        )
        for f in group.fields
    }
    if group.is_array:
        schema: dict[str, Any] = {
            "type": "object",
            "required": ["rows"],
            "properties": {
                "rows": {
                    "type": "array",
                    "description": "One object per row, in document order.",
                    "items": {"type": "object", "properties": props},
                }
            },
        }
    else:
        schema = {
            "type": "object",
            "required": ["fields"],
            "properties": {"fields": {"type": "object", "properties": props}},
        }
    return ToolSpec(
        name="record_fields",
        description=f"Record the extracted values for the '{group.name}' field group.",
        input_schema=schema,
    )


def model_label(model_id: str, tier: ModelTier) -> str:
    low = model_id.lower()
    for family in ("haiku", "sonnet", "opus"):
        if family in low:
            return family
    return tier.value


def _table_pages(block: Block) -> list[tuple[Block, str]]:
    """Large tables are sent 40 rows at a time with the header repeated."""
    from formwork_ingest.base import markdown_table

    if not block.cells or len(block.cells) - 1 <= TABLE_PAGE_ROWS:
        return [(block, block.text)]
    header, rows = block.cells[0], block.cells[1:]
    return [
        (block, markdown_table([header, *rows[i : i + TABLE_PAGE_ROWS]]))
        for i in range(0, len(rows), TABLE_PAGE_ROWS)
    ]


class LLMExtractor:
    name: ClassVar[str] = "text"
    prefix: ClassVar[str] = "llm"
    default_tier: ClassVar[ModelTier] = ModelTier.CHEAP
    with_images: ClassVar[bool] = False

    async def extract(self, group: FieldGroup, blocks: list[Block], ctx: JobContext) -> list[Extraction]:
        return (await self.run(group, blocks, ctx)).extractions

    async def run(
        self,
        group: FieldGroup,
        blocks: list[Block],
        ctx: JobContext,
        *,
        tier: ModelTier | None = None,
        feedback: str | None = None,
        doc_class: str | None = None,
        record_key: str | None = None,
        attempt: int = 1,
    ) -> ExtractionReport:
        report = ExtractionReport()
        if not blocks or not group.fields:
            return report
        tier = tier or self.default_tier
        # Array groups: page big tables; everything else goes in one call.
        calls: list[list[tuple[Block, str]]] = []
        if group.is_array:
            small: list[tuple[Block, str]] = []
            for b in blocks:
                pages = (
                    _table_pages(b) if b.type in (BlockType.TABLE, BlockType.SHEET_RANGE) else [(b, b.text)]
                )
                if len(pages) > 1:
                    calls.extend([[p] for p in pages])
                else:
                    small.extend(pages)
            if small:
                calls.insert(0, small)
        else:
            calls = [[(b, b.text) for b in blocks]]
        row_offset = 0
        for call_blocks in calls:
            row_offset += await self._call(
                group, call_blocks, ctx, tier, feedback, doc_class, record_key, attempt, row_offset, report
            )
        return report

    async def _call(
        self,
        group: FieldGroup,
        call_blocks: list[tuple[Block, str]],
        ctx: JobContext,
        tier: ModelTier,
        feedback: str | None,
        doc_class: str | None,
        record_key: str | None,
        attempt: int,
        row_offset: int,
        report: ExtractionReport,
    ) -> int:
        by_id = {b.id: b for b, _ in call_blocks}
        flagged: dict[str, list[tuple[int, int]]] = {}
        rendered = []
        for b, text in call_blocks:
            safe, spans = mask(text)
            if spans:
                flagged[b.id] = mask(b.text)[1]
                log.warning(
                    "instruction_like_text_masked", block_id=b.id, document_id=b.document_id, spans=len(spans)
                )
            rendered.append(render_block(b, safe))
        images: list[bytes] = []
        if self.with_images:
            for b, _ in call_blocks[:MAX_IMAGES]:
                try:
                    images.append(await ctx.store.get(crop_key(ctx.tenant_id, b.document_id, b.id)))
                except NotFound:
                    continue
        system = f"{_RULES}\nFields to extract:\n<fields>\n{json.dumps(field_listing(group), ensure_ascii=False)}\n</fields>"
        parts = []
        if doc_class:
            parts.append(f"Document class: {doc_class}")
        if images:
            parts.append(
                "Images of the low-confidence regions are attached in block order; prefer what you see over the OCR text."
            )
        if feedback:
            parts.append(
                f"<feedback>\nA previous attempt had problems. Fix them using only the document:\n{feedback}\n</feedback>"
            )
        parts.append("\n".join(rendered))
        request = LLMRequest(
            tier=tier,
            system=system,
            prompt="\n".join(parts),
            images=images,
            tool=tool_for(group),
            purpose=f"extract:{self.name}" if attempt == 1 else f"extract:repair:{self.name}",
            max_tokens=4096,
        )
        with span(
            "stage.extract.call",
            tenant_id=ctx.tenant_id,
            job_id=ctx.job_id,
            extractor=self.name,
            group=group.name,
        ):
            resp = await ctx.provider.complete(request)
        label = f"{self.prefix}:{model_label(resp.model, tier)}"
        specs = group.by_key()

        def take(key: str, item: Any, index: int | None) -> None:
            spec = specs.get(key)
            if spec is None or not isinstance(item, dict) or item.get("value") is None:
                return
            path = concrete_path(spec.path, index) if index is not None else spec.path
            self._accept(spec, path, item, by_id, flagged, label, record_key, attempt, report)

        if group.is_array:
            rows = resp.output.get("rows") or []
            rows = rows if isinstance(rows, list) else []
            for i, row in enumerate(rows):
                if isinstance(row, dict):
                    for key, item in row.items():
                        take(key, item, row_offset + i)
            return len(rows)
        fields = resp.output.get("fields") or {}
        for key, item in fields.items() if isinstance(fields, dict) else []:
            take(key, item, None)
        return 0

    def _accept(
        self,
        spec: FieldSpec,
        path: str,
        item: dict[str, Any],
        by_id: dict[str, Block],
        flagged: dict[str, list[tuple[int, int]]],
        label: str,
        record_key: str | None,
        attempt: int,
        report: ExtractionReport,
    ) -> None:
        value: Scalar = item.get("value")
        quote = str(item.get("quote") or "")
        block = by_id.get(str(item.get("block_id")))
        doc_id = block.document_id if block else next(iter(by_id.values())).document_id

        def reject(reason: str) -> None:
            report.rejected.append(RejectedValue(path, doc_id, value, quote[:300], reason))
            log.info("value_rejected", field=path, reason=reason, document_id=doc_id)

        if block is None:
            return reject("unknown_block")
        ratio = quote_match(quote, block.text)
        if ratio < GROUNDING_MIN:
            return reject("ungrounded")
        if block.id in flagged and overlaps(quote, block.text, flagged[block.id]):
            return reject("untrusted_instruction")
        if not value_supported(value, quote, spec):
            return reject("value_not_in_quote")
        try:
            model_conf = min(1.0, max(0.0, float(item.get("self_confidence", 0.5))))
        except (TypeError, ValueError):
            model_conf = 0.5
        signals = {MODEL: model_conf, GROUNDING: ratio}
        if block.ocr_confidence is not None:
            signals[OCR] = block.ocr_confidence
        evidence = [
            Evidence(
                block_id=block.id,
                document_id=block.document_id,
                bbox=block.bbox,
                locator=block.locator,
                quote=quote.strip(),
            )
        ]
        norm, err = normalize(value, spec)
        key = record_key or f"{PROVISIONAL_PREFIX}{block.document_id}"
        if err:
            report.extractions.append(
                Extraction(
                    record_key=key,
                    field_path=path,
                    value=value,
                    normalized_value=None,
                    confidence=min(blend(signals), 0.5),
                    evidence=evidence,
                    extractor=label,
                    attempt=attempt,
                    status=ExtractionStatus.FAILED_VALIDATION,
                    reason=err,
                    signals=signals,
                )
            )
        else:
            report.extractions.append(
                Extraction(
                    record_key=key,
                    field_path=path,
                    value=typed(norm, spec),
                    normalized_value=norm,
                    confidence=blend(signals),
                    evidence=evidence,
                    extractor=label,
                    attempt=attempt,
                    signals=signals,
                )
            )
