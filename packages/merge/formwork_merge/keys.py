"""Step 1 of merge: give every candidate its record key from the normalised entity-key values of its document."""

from __future__ import annotations

from formwork_core.models import Extraction, TargetSchema
from formwork_core.paths import record_key

PROVISIONAL_PREFIX = "doc:"
UNKEYED_PREFIX = "unkeyed-"


def resolve_keys(schema: TargetSchema, candidates: list[Extraction]) -> list[Extraction]:
    """LLM extractors emit 'doc:<document_id>' keys (one record per document). Replace them with the real key."""
    by_doc: dict[str, dict[str, Extraction]] = {}
    for e in candidates:
        if (
            e.record_key.startswith(PROVISIONAL_PREFIX)
            and e.field_path in schema.entity_key
            and e.normalized_value is not None
        ):
            by_doc.setdefault(e.record_key, {})[e.field_path] = e
    out = []
    for e in candidates:
        if e.record_key.startswith(PROVISIONAL_PREFIX):
            found = by_doc.get(e.record_key, {})
            values: list[object] = [found[k].normalized_value for k in schema.entity_key if k in found]
            key = record_key(values) if len(values) == len(schema.entity_key) and values else ""
            e = e.model_copy(
                update={"record_key": key or UNKEYED_PREFIX + e.record_key[len(PROVISIONAL_PREFIX) :]}
            )
        out.append(e)
    return out
