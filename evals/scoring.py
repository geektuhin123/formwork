"""Field-level scoring: exact match on normalised values against expected.json."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from formwork_core.models import TargetSchema
from formwork_core.normalize import normalize, values_agree
from formwork_output.records import flatten


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    per_field: dict[str, list[int]] = field(default_factory=dict)  # spec path -> [tp, fp, fn]

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def score_records(
    schema: TargetSchema, expected: dict[str, dict[str, Any]], predicted: dict[str, dict[str, Any]]
) -> Score:
    """A wrong value counts as both a false positive and a false negative; a missing one as a false negative."""
    from formwork_core.paths import spec_path

    s = Score()
    for key in sorted(set(expected) | set(predicted)):
        exp = {p: v for p, v in flatten(expected.get(key, {})).items() if v is not None}
        got = {p: v for p, v in flatten(predicted.get(key, {})).items() if v is not None}
        for path in sorted(set(exp) | set(got)):
            spec = schema.field(path)
            if spec is None:
                continue
            cell = s.per_field.setdefault(spec_path(path), [0, 0, 0])
            e_norm = normalize(exp[path], spec)[0] if path in exp else None
            g_norm = normalize(got[path], spec)[0] if path in got else None
            if e_norm is not None and g_norm is not None and values_agree(e_norm, g_norm, spec):
                s.tp += 1
                cell[0] += 1
                continue
            if path in got:
                s.fp += 1
                cell[1] += 1
            if path in exp:
                s.fn += 1
                cell[2] += 1
            s.errors.append({"record": key, "field": path, "expected": exp.get(path), "got": got.get(path)})
    return s
