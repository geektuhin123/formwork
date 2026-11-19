"""Cross-source merge: one record set out of many documents (docs/lld.md, "6. Cross-source merge")."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from formwork_core.models import (
    Alternative,
    Evidence,
    Extraction,
    ExtractionStatus,
    FieldSpec,
    FieldType,
    TargetSchema,
)
from formwork_core.normalize import typed, values_agree
from formwork_core.paths import array_index, array_root
from rapidfuzz import fuzz

from formwork_merge.keys import UNKEYED_PREFIX, resolve_keys

FUZZY_KEY_MIN = 92
AGREEMENT = "agreement"
_CONFUSABLE = str.maketrans({"o": "0", "i": "1", "l": "1", "s": "5", "b": "8", "z": "2", "g": "9"})


class MergePolicy(StrEnum):
    PREFER_HIGHEST_CONFIDENCE = "prefer_highest_confidence"
    PREFER_LATEST_SOURCE = "prefer_latest_source"
    PREFER_SOURCE_PRIORITY = "prefer_source_priority"
    REQUIRE_AGREEMENT = "require_agreement"
    SUM = "sum"
    CONCAT_UNIQUE = "concat_unique"


@dataclass
class SourceInfo:
    document_id: str
    doc_class: str | None = None
    filename: str = ""
    document_date: str | None = None  # ISO date; newest wins under prefer_latest_source
    priority: int = 1000  # lower wins under prefer_source_priority
    order: int = 0  # upload order, the tie-break


@dataclass
class MergeResult:
    extractions: list[Extraction]
    key_aliases: dict[str, str] = field(default_factory=dict)  # fuzzy-matched key -> canonical key
    record_sources: dict[str, list[str]] = field(default_factory=dict)  # record key -> document ids
    unkeyed_documents: list[str] = field(default_factory=list)


def _doc(e: Extraction) -> str:
    return e.evidence[0].document_id


def _digits(key: str) -> str:
    return re.sub(r"\D", "", key)


def match_keys(doc_keys: dict[str, set[str]]) -> dict[str, str]:
    """Exact keys group on their own. A key seen in only one document may join another key when they are the same
    after OCR-confusable folding, or token-set ratio >= 92 with identical digit sequences. Deliberately conservative:
    a wrong entity match merges two records, which is worse than leaving one for review."""
    aliases: dict[str, str] = {}
    keys = sorted(
        (k for k in doc_keys if not k.startswith(UNKEYED_PREFIX)), key=lambda k: (-len(doc_keys[k]), k)
    )
    for k in keys:
        if len(doc_keys[k]) != 1 or k in aliases:
            continue
        for other in keys:
            if other == k or other in aliases or aliases.get(other) == k:
                continue
            if doc_keys[other] & doc_keys[k]:
                continue  # two keys from the same document are two records
            same_folded = k.translate(_CONFUSABLE) == other.translate(_CONFUSABLE)
            close = (
                fuzz.token_set_ratio(k.replace("-", " "), other.replace("-", " ")) >= FUZZY_KEY_MIN
                and _digits(k) == _digits(other)
                and bool(_digits(k))
            )
            if same_folded or close:
                aliases[k] = other
                break
    return aliases


def _cluster(cands: list[Extraction], spec: FieldSpec) -> list[list[Extraction]]:
    clusters: list[list[Extraction]] = []
    for c in sorted(cands, key=lambda x: -x.confidence):
        for cl in clusters:
            if values_agree(cl[0].normalized_value, c.normalized_value, spec):
                cl.append(c)
                break
        else:
            clusters.append([c])
    return clusters


def _alt(e: Extraction) -> Alternative:
    return Alternative(
        value=e.value,
        normalized_value=e.normalized_value,
        confidence=e.confidence,
        extractor=e.extractor,
        evidence=e.evidence,
    )


def _union_evidence(cluster: list[Extraction]) -> list[Evidence]:
    seen: set[tuple[str, str]] = set()
    out = []
    for c in cluster:
        for ev in c.evidence:
            if (ev.block_id, ev.quote) not in seen:
                seen.add((ev.block_id, ev.quote))
                out.append(ev)
    return out


def resolve_field(cands: list[Extraction], spec: FieldSpec, sources: dict[str, SourceInfo]) -> Extraction:
    """Resolve one (record, field) from all its candidates using the field's merge policy. Losers stay as alternatives."""
    valid = [c for c in cands if c.normalized_value is not None]
    if not valid:  # nothing usable: surface the best failed candidate for review
        best = max(cands, key=lambda c: c.confidence)
        return best.model_copy(
            update={"alternatives": [*best.alternatives, *[_alt(c) for c in cands if c is not best]]}
        )
    try:
        policy = MergePolicy(spec.merge_policy)
    except ValueError:
        policy = MergePolicy.PREFER_HIGHEST_CONFIDENCE
    clusters = _cluster(valid, spec)
    n_docs = len({_doc(c) for c in valid})

    def src(c: Extraction) -> SourceInfo:
        return sources.get(_doc(c)) or SourceInfo(_doc(c))

    if policy is MergePolicy.SUM and spec.type in (FieldType.NUMBER, FieldType.INTEGER):
        per_doc: dict[str, Extraction] = {}
        for c in valid:
            per_doc.setdefault(_doc(c), c)
        try:
            total = sum((Decimal(c.normalized_value or "0") for c in per_doc.values()), Decimal(0))
        except InvalidOperation:
            total = Decimal(0)
        norm = format(total.normalize(), "f")
        best = max(per_doc.values(), key=lambda c: c.confidence)
        return best.model_copy(
            update={
                "value": typed(norm, spec),
                "normalized_value": norm,
                "evidence": _union_evidence(list(per_doc.values())),
                "confidence": min(c.confidence for c in per_doc.values()),
            }
        )
    if policy is MergePolicy.CONCAT_UNIQUE:
        parts = [cl[0].normalized_value or "" for cl in clusters]
        best = clusters[0][0]
        return best.model_copy(
            update={
                "value": "; ".join(parts),
                "normalized_value": "; ".join(parts),
                "evidence": _union_evidence(valid),
            }
        )

    if policy is MergePolicy.PREFER_LATEST_SOURCE:
        rank = lambda cl: max((src(c).document_date or "", src(c).order) for c in cl)  # noqa: E731
        winner = max(clusters, key=rank)
    elif policy is MergePolicy.PREFER_SOURCE_PRIORITY:
        winner = min(clusters, key=lambda cl: min((src(c).priority, src(c).order) for c in cl))
    else:
        winner = (
            max(clusters, key=lambda cl: (len({_doc(c) for c in cl}), cl[0].confidence))
            if policy is MergePolicy.REQUIRE_AGREEMENT
            else clusters[0]
        )
    best = max(winner, key=lambda c: c.confidence)
    losers = [cl for cl in clusters if cl is not winner]
    alternatives = [*best.alternatives, *[_alt(max(cl, key=lambda c: c.confidence)) for cl in losers]]
    signals = dict(best.signals)
    if n_docs > 1:
        signals[AGREEMENT] = len({_doc(c) for c in winner}) / n_docs
    status, reason = best.status, best.reason
    if policy is MergePolicy.REQUIRE_AGREEMENT and losers:
        status = ExtractionStatus.CONFLICT
        reason = "sources disagree: " + " vs ".join(repr(cl[0].value) for cl in clusters)
    from formwork_extract.confidence import blend

    return best.model_copy(
        update={
            "evidence": _union_evidence(winner),
            "alternatives": alternatives,
            "signals": signals,
            "confidence": blend(signals) if signals else best.confidence,
            "status": status,
            "reason": reason,
        }
    )


def merge(
    schema: TargetSchema, candidates: list[Extraction], sources: dict[str, SourceInfo] | None = None
) -> MergeResult:
    sources = sources or {}
    keyed = resolve_keys(schema, candidates)
    doc_keys: dict[str, set[str]] = {}
    for e in keyed:
        doc_keys.setdefault(e.record_key, set()).add(_doc(e))
    aliases = match_keys(doc_keys)
    keyed = [
        e.model_copy(update={"record_key": aliases[e.record_key]}) if e.record_key in aliases else e
        for e in keyed
    ]

    # Arrays: when sources disagree on the number of rows, rows cannot be aligned by index; keep the best source whole.
    rows: dict[tuple[str, str], dict[str, list[Extraction]]] = {}
    for e in keyed:
        root = array_root(e.field_path)
        if root is not None:
            rows.setdefault((e.record_key, root), {}).setdefault(_doc(e), []).append(e)
    dropped: set[int] = set()
    for per_doc in rows.values():
        counts = {d: max((array_index(e.field_path) or 0) for e in es) + 1 for d, es in per_doc.items()}
        if len(set(counts.values())) > 1:
            keep = max(
                per_doc,
                key=lambda d: (
                    sum(e.confidence for e in per_doc[d]) / len(per_doc[d]),
                    -sources.get(d, SourceInfo(d)).order,
                ),
            )
            dropped.update(id(e) for d, es in per_doc.items() if d != keep for e in es)

    grouped: dict[tuple[str, str], list[Extraction]] = {}
    record_sources: dict[str, list[str]] = {}
    for e in keyed:
        if id(e) in dropped:
            continue
        grouped.setdefault((e.record_key, e.field_path), []).append(e)
        if _doc(e) not in record_sources.setdefault(e.record_key, []):
            record_sources[e.record_key].append(_doc(e))
    out = []
    for (_key, path), cands in sorted(grouped.items()):
        spec = schema.field(path)
        if spec is None:
            continue
        out.append(resolve_field(cands, spec, sources))
    unkeyed = sorted({d for k, ds in record_sources.items() if k.startswith(UNKEYED_PREFIX) for d in ds})
    return MergeResult(out, aliases, record_sources, unkeyed)
