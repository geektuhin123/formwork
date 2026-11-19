"""Validate and repair (docs/lld.md, "7. Validate and repair").

Field checks (type, format, enum, required) -> rule checks (simpleeval) -> repair loop (at most 2 attempts per field,
wider window, next model up, budget-aware) -> final statuses. Anything still failing goes to review.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from formwork_core.errors import BudgetExceeded
from formwork_core.logging import get_logger
from formwork_core.models import Alternative, Extraction, ExtractionStatus, FieldSpec, FieldType, TargetSchema
from formwork_core.normalize import normalize, typed
from formwork_core.paths import array_index, array_root, concrete_path

from formwork_validate.rules import RuleNotEvaluable, evaluate

log = get_logger("formwork.validate")
RULES = "rules"
OCR_FIX_CONFIDENCE_CAP = 0.85
_CONFUSABLE = {
    "O": "0",
    "0": "O",
    "I": "1",
    "1": "I",
    "l": "1",
    "S": "5",
    "5": "S",
    "B": "8",
    "8": "B",
    "Z": "2",
    "2": "Z",
    "G": "6",
    "6": "G",
}


@dataclass
class Gap:
    record_key: str
    field_path: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"record_key": self.record_key, "field_path": self.field_path, "reason": self.reason}


@dataclass
class RepairTarget:
    record_key: str
    field_path: str
    error: str
    current: Extraction | None


class Repairer(Protocol):
    async def repair(self, targets: list[RepairTarget], attempt: int) -> list[Extraction]:
        """Re-extract the targets with the error message, a wider block window and the next model up."""
        ...


@dataclass
class ValidateOptions:
    auto_approve_threshold: float = 0.9
    repair_threshold: float = 0.75
    max_repair_attempts: int = 2


@dataclass
class ValidationResult:
    extractions: list[Extraction]
    gaps: list[Gap] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def ocr_confusion_fix(value: str, pattern: str) -> str | None:
    """A value that misses its format by OCR look-alikes (O/0, I/1, S/5, B/8) and has exactly one fix is corrected."""
    spots = [i for i, ch in enumerate(value) if ch in _CONFUSABLE]
    if not spots or len(spots) > 8:
        return None
    hits: set[str] = set()
    for n in (1, 2):
        for combo in itertools.combinations(spots, n):
            chars = list(value)
            for i in combo:
                chars[i] = _CONFUSABLE[chars[i]]
            cand = "".join(chars)
            if re.fullmatch(pattern, cand):
                hits.add(cand)
        if hits:
            break
    return hits.pop() if len(hits) == 1 else None


def _blend(signals: dict[str, float]) -> float:
    known = [min(1.0, max(0.0, v)) for v in signals.values()]
    return round(sum(known) / len(known), 4) if known else 0.0


def check_fields(schema: TargetSchema, extractions: list[Extraction]) -> list[Extraction]:
    """Type coercion, format, enum. Failures become failed_validation (kept with evidence for review)."""
    out = []
    for e in extractions:
        spec = schema.field(e.field_path)
        if spec is None or e.status in (ExtractionStatus.APPROVED,):
            out.append(e)
            continue
        norm, err = normalize(e.value, spec)
        update: dict[str, Any] = {}
        if err and spec.format and isinstance(e.value, str) and "format" in err:
            fixed = ocr_confusion_fix(e.value.strip(), spec.format)
            if fixed is not None:
                signals = {**e.signals, "ocr_fix": 0.6}
                update = {
                    "value": fixed,
                    "normalized_value": fixed,
                    "signals": signals,
                    "reason": f"ocr_corrected from {e.value!r}",
                    "confidence": min(_blend(signals), OCR_FIX_CONFIDENCE_CAP),
                    "status": ExtractionStatus.EXTRACTED,
                }
                err = None
        if err and not update:
            update = {
                "normalized_value": None,
                "status": ExtractionStatus.FAILED_VALIDATION,
                "reason": err,
                "confidence": min(e.confidence, 0.5),
            }
        elif (
            not update
            and e.status is ExtractionStatus.FAILED_VALIDATION
            and not (e.reason or "").startswith("rule")
        ):
            update = {
                "normalized_value": norm,
                "value": typed(norm, spec),
                "status": ExtractionStatus.EXTRACTED,
                "reason": None,
            }
        elif not update and e.normalized_value != norm:
            update = {"normalized_value": norm}
        out.append(e.model_copy(update=update) if update else e)
    return out


def _names(schema: TargetSchema, values: dict[str, Extraction]) -> dict[str, Any]:
    """Rule namespace for one record: scalar fields by name (dots -> underscores), arrays as lists of row dicts."""
    names: dict[str, Any] = {}
    for f in schema.fields:
        if f.type is FieldType.ARRAY:
            rows: dict[int, dict[str, Any]] = {}
            for path, e in values.items():
                if array_root(path) == f.path and e.normalized_value is not None:
                    rows.setdefault(array_index(path) or 0, {})[path.split("].", 1)[-1].replace(".", "_")] = (
                        e.value
                    )
            names[f.name] = [rows[i] for i in sorted(rows)]
        else:
            for leaf in [f] if not f.children else _leaves(f):
                e2 = values.get(leaf.path)
                names[leaf.path.replace(".", "_")] = (
                    e2.value if e2 is not None and e2.normalized_value is not None else None
                )
    return names


def _leaves(f: FieldSpec) -> list[FieldSpec]:
    return [f] if not f.children else [x for c in f.children for x in _leaves(c)]


def check_rules(schema: TargetSchema, extractions: list[Extraction]) -> list[Extraction]:
    """Evaluate FieldSpec.rules per record (and per row for array children). Sets the `rules` signal and status."""
    by_record: dict[str, dict[str, Extraction]] = {}
    for e in extractions:
        by_record.setdefault(e.record_key, {})[e.field_path] = e
    ruled = [f for f in schema.leaf_fields() if f.rules]
    for values in by_record.values():
        names = _names(schema, values)
        for spec in ruled:
            if "[]" in spec.path:
                root = spec.path.split("[]", 1)[0]
                targets = [
                    (concrete_path(spec.path, i), {**names, **row})
                    for i, row in enumerate(names.get(root.split(".")[-1], []))
                ]
            else:
                targets = [(spec.path, names)]
            for path, ns in targets:
                cur = values.get(path)
                if (
                    cur is None
                    or cur.normalized_value is None
                    or cur.status in (ExtractionStatus.CONFLICT, ExtractionStatus.APPROVED)
                ):
                    continue
                failures, evaluated = [], 0
                for rule in spec.rules:
                    try:
                        ok = evaluate(rule, ns)
                    except RuleNotEvaluable as exc:
                        log.debug("rule_skipped", rule=rule, reason=str(exc))
                        continue
                    evaluated += 1
                    if not ok:
                        failures.append(rule)
                if not evaluated:
                    continue
                signals = {**cur.signals, RULES: 0.0 if failures else 1.0}
                update: dict[str, Any] = {
                    "signals": signals,
                    "confidence": min(_blend(signals), cur.confidence + 0.2),
                }
                if failures:
                    update.update(
                        status=ExtractionStatus.FAILED_VALIDATION,
                        reason="rule failed: " + "; ".join(failures),
                    )
                elif cur.status is ExtractionStatus.FAILED_VALIDATION and (cur.reason or "").startswith(
                    "rule"
                ):
                    update.update(status=ExtractionStatus.EXTRACTED, reason=None)
                values[path] = cur.model_copy(update=update)
    return [e for values in by_record.values() for e in values.values()]


def find_gaps(
    schema: TargetSchema, extractions: list[Extraction], extra: list[Gap] | None = None
) -> list[Gap]:
    """Required fields with no value, per record; plus fields whose only value was rejected by grounding."""
    have: dict[str, set[str]] = {}
    rows: dict[tuple[str, str], set[int]] = {}
    for e in extractions:
        have.setdefault(e.record_key, set()).add(e.field_path)
        root = array_root(e.field_path)
        if root is not None:
            rows.setdefault((e.record_key, root), set()).add(array_index(e.field_path) or 0)
    gaps: list[Gap] = []
    for key, paths in have.items():
        for spec in schema.leaf_fields():
            if not spec.required:
                continue
            if "[]" in spec.path:
                root = spec.path.split("[]", 1)[0]
                for i in sorted(rows.get((key, root), set())):
                    if concrete_path(spec.path, i) not in paths:
                        gaps.append(Gap(key, concrete_path(spec.path, i), "required field is missing"))
            elif spec.path not in paths:
                gaps.append(Gap(key, spec.path, "required field is missing"))
        for f in schema.fields:
            if f.type is FieldType.ARRAY and f.required and not rows.get((key, f.path)):
                gaps.append(Gap(key, f.path, "required rows are missing"))
    seen = {(g.record_key, g.field_path) for g in gaps}
    for g in extra or []:
        if g.field_path not in have.get(g.record_key, set()) and (g.record_key, g.field_path) not in seen:
            gaps.append(g)
            seen.add((g.record_key, g.field_path))
    return gaps


def finalize(extractions: list[Extraction], threshold: float) -> list[Extraction]:
    """Auto-approve what is valid, unconflicted and confident; everything else waits for a reviewer."""
    out = []
    for e in extractions:
        if e.status in (
            ExtractionStatus.APPROVED,
            ExtractionStatus.CONFLICT,
            ExtractionStatus.FAILED_VALIDATION,
        ):
            out.append(e)
        elif e.normalized_value is not None and e.confidence >= threshold:
            out.append(e.model_copy(update={"status": ExtractionStatus.APPROVED}))
        else:
            out.append(
                e.model_copy(
                    update={
                        "status": ExtractionStatus.NEEDS_REVIEW,
                        "reason": e.reason
                        or f"confidence {e.confidence:.2f} is below the auto-approve threshold",
                    }
                )
            )
    return out


def run_checks(schema: TargetSchema, extractions: list[Extraction]) -> list[Extraction]:
    return check_rules(schema, check_fields(schema, extractions))


async def validate_and_repair(
    schema: TargetSchema,
    extractions: list[Extraction],
    repairer: Repairer | None = None,
    options: ValidateOptions | None = None,
    rejected: list[Gap] | None = None,
) -> ValidationResult:
    opts = options or ValidateOptions()
    current = run_checks(schema, extractions)
    gaps = find_gaps(schema, current, rejected)
    stats: dict[str, Any] = {"repair_attempts": 0, "repaired": 0, "budget_exhausted": False}
    attempts: dict[tuple[str, str], int] = {}
    if repairer is not None:
        for attempt in range(1, opts.max_repair_attempts + 1):
            targets = [
                RepairTarget(
                    e.record_key, e.field_path, e.reason or f"low confidence ({e.confidence:.2f})", e
                )
                for e in current
                if e.status is ExtractionStatus.FAILED_VALIDATION
                or (e.status is ExtractionStatus.EXTRACTED and e.confidence < opts.repair_threshold)
            ]
            targets += [RepairTarget(g.record_key, g.field_path, g.reason, None) for g in gaps]
            targets = [
                t for t in targets if attempts.get((t.record_key, t.field_path), 0) < opts.max_repair_attempts
            ]
            if not targets:
                break
            for t in targets:
                attempts[(t.record_key, t.field_path)] = attempts.get((t.record_key, t.field_path), 0) + 1
            stats["repair_attempts"] += len(targets)
            try:
                fixes = await repairer.repair(targets, attempt)
            except (
                BudgetExceeded
            ):  # escalation stops once the budget is reached; the remaining fields go to review
                stats["budget_exhausted"] = True
                log.warning("repair_stopped_budget", remaining=len(targets))
                break
            if not fixes:
                continue
            index = {(e.record_key, e.field_path): e for e in current}
            before = {k: (v.status, v.normalized_value) for k, v in index.items()}
            for fix in fixes:
                k = (fix.record_key, fix.field_path)
                old = index.get(k)
                if fix.normalized_value is None or (
                    old is not None and old.normalized_value == fix.normalized_value
                ):
                    continue  # a re-read that says the same thing changes nothing; keep the merged evidence
                alts = list(fix.alternatives)
                if old is not None:
                    alts += old.alternatives
                    if old.normalized_value is not None and old.normalized_value != fix.normalized_value:
                        alts.append(
                            Alternative(
                                value=old.value,
                                normalized_value=old.normalized_value,
                                confidence=old.confidence,
                                extractor=old.extractor,
                                evidence=old.evidence,
                            )
                        )
                index[k] = fix.model_copy(
                    update={
                        "attempt": attempt + 1,
                        "alternatives": alts,
                        "status": ExtractionStatus.EXTRACTED,
                        "reason": None,
                    }
                )
            current = run_checks(schema, list(index.values()))
            after = {(e.record_key, e.field_path): e for e in current}
            stats["repaired"] += sum(
                1
                for k, e in after.items()
                if e.status is ExtractionStatus.EXTRACTED
                and e.normalized_value is not None
                and before.get(k, (None, None)) != (e.status, e.normalized_value)
            )
            gaps = find_gaps(schema, current, rejected)
    final = finalize(current, opts.auto_approve_threshold)
    stats.update(
        fields_total=len(final) + len(gaps),
        auto_approved=sum(1 for e in final if e.status is ExtractionStatus.APPROVED),
        in_review=sum(1 for e in final if e.status is not ExtractionStatus.APPROVED) + len(gaps),
        conflicts=sum(1 for e in final if e.status is ExtractionStatus.CONFLICT),
        gaps=len(gaps),
    )
    return ValidationResult(sorted(final, key=lambda e: (e.record_key, e.field_path)), gaps, stats)
