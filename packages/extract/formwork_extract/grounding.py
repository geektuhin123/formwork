"""Grounding check: the main defence against hallucination.

A value is kept only if (1) its quote appears in the cited block (rapidfuzz ratio >= 0.9) and (2) the value is
actually supported by that quote (the number/date/text it claims is in the quoted span).
"""

from __future__ import annotations

import re
from decimal import Decimal

from formwork_core.models import FieldSpec, FieldType, Scalar
from formwork_core.normalize import comparison_key, parse_date, parse_number
from rapidfuzz import fuzz

GROUNDING_MIN = 0.9
_WS = re.compile(r"\s+")
_NUM = re.compile(r"\(?[-+]?\d[\d,]*(?:\.\d+)?\)?")
_DATEISH = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[/\-. ]\w{1,9}[/\-., ]+\d{2,4}|[A-Za-z]{3,9} \d{1,2},? \d{4}"
)


def _norm(text: str) -> str:
    return _WS.sub(" ", text.replace("|", " ")).strip().casefold()


def quote_match(quote: str, block_text: str) -> float:
    """0..1: how well the quote is found inside the block text."""
    q, t = _norm(quote), _norm(block_text)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    if len(q) < 8:  # short quotes fuzzy-match anything; require them verbatim
        return 0.0
    return float(fuzz.partial_ratio(q, t)) / 100.0


def value_supported(value: Scalar, quote: str, spec: FieldSpec) -> bool:
    """Is the value the model returned actually present in the span it quoted?"""
    if value is None:
        return False
    if spec.type in (FieldType.NUMBER, FieldType.INTEGER):
        want = parse_number(value)
        if want is None:
            return False
        for m in _NUM.finditer(quote):
            got = parse_number(m.group())
            if got is not None and abs(got - want) <= Decimal("0.005"):
                return True
        return False
    if spec.type in (FieldType.DATE, FieldType.DATETIME):
        want_d = parse_date(value)
        if want_d is None:
            return False
        return any(parse_date(m.group()) == want_d for m in _DATEISH.finditer(quote))
    if spec.type in (FieldType.BOOLEAN, FieldType.ENUM, FieldType.CURRENCY):
        return True  # derived values (a tick mark -> true, '₹' -> INR); the quote check already anchors them
    v, q = comparison_key(str(value)), comparison_key(quote)
    if not v:
        return False
    return v in q or (len(v) >= 8 and fuzz.partial_ratio(v, q) >= 88)
