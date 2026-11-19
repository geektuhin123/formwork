"""Confidence = weighted blend of the signals that are known for a value.

    c = w1*s_model + w2*g_match + w3*o_ocr + w4*v_rules + w5*a_agree

Equal weights for now (LLD); fit them with logistic regression once 300+ fields are labelled. A signal that is not
known yet (rules before validation, agreement with a single source) is left out and the rest are re-normalised.
"""

from __future__ import annotations

MODEL, GROUNDING, OCR, RULES, AGREEMENT = "model", "grounding", "ocr", "rules", "agreement"
DEFAULT_WEIGHTS: dict[str, float] = {MODEL: 1.0, GROUNDING: 1.0, OCR: 1.0, RULES: 1.0, AGREEMENT: 1.0}


def blend(signals: dict[str, float], weights: dict[str, float] | None = None) -> float:
    w = weights or DEFAULT_WEIGHTS
    total = sum(w[k] for k in signals if k in w)
    if total <= 0:
        return 0.0
    score = sum(w[k] * min(1.0, max(0.0, v)) for k, v in signals.items() if k in w) / total
    return round(min(1.0, max(0.0, score)), 4)
