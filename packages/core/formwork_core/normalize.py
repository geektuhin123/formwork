"""Type-aware value normalisation shared by the tabular mapper, merge, validate and the eval scorer."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

from formwork_core.models import FieldSpec, FieldType, Scalar

_CURRENCY = {
    "₹": "INR",
    "RS": "INR",
    "RS.": "INR",
    "INR": "INR",
    "$": "USD",
    "USD": "USD",
    "US$": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "AED": "AED",
    "SGD": "SGD",
    "JPY": "JPY",
    "¥": "JPY",
}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_TRUE = {"true", "yes", "y", "1", "✓", "checked"}
_FALSE = {"false", "no", "n", "0", "unchecked"}


def parse_number(value: Scalar) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))
    s = unicodedata.normalize("NFKC", str(value)).strip()
    neg = (s.startswith("(") and s.endswith(")")) or s.endswith("-") or s.upper().endswith(" DR")
    s = re.sub(r"(?i)\b(rs\.?|inr|usd|eur|gbp|cr|dr)(?![a-z])", "", s)
    s = re.sub(r"[₹$€£¥,\s()]", "", s).rstrip("-")
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?|[-+]?\.\d+", s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg and d > 0 else d


def _fmt_decimal(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


def parse_date(value: Scalar, dayfirst: bool = True) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or isinstance(value, bool | int | float):
        return None
    s = str(value).strip()
    if not s or not re.search(r"\d", s) or len(s) > 40:
        return None
    try:
        if _ISO_DATE.match(s):
            return date_parser.isoparse(s[:10]).date()
        return date_parser.parse(s, dayfirst=dayfirst, fuzzy=False).date()
    except (ValueError, OverflowError):
        return None


def normalize(value: Scalar, spec: FieldSpec) -> tuple[str | None, str | None]:
    """-> (normalized string, error). Exactly one of them is None unless the value itself is None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "empty"
    t = spec.type
    if t in (FieldType.NUMBER, FieldType.INTEGER):
        d = parse_number(value)
        if d is None:
            return None, f"{value!r} is not a number"
        if t is FieldType.INTEGER and d != d.to_integral_value():
            return None, f"{value!r} is not a whole number"
        return _fmt_decimal(d), None
    if t in (FieldType.DATE, FieldType.DATETIME):
        dayfirst = not (spec.format or "").upper().startswith("MM")
        dt = parse_date(value, dayfirst=dayfirst)
        if dt is None:
            return None, f"{value!r} is not a date"
        if not 1900 <= dt.year <= 2100:
            return None, f"{value!r} is outside the plausible date range"
        return dt.isoformat(), None
    if t is FieldType.BOOLEAN:
        if isinstance(value, bool):
            return ("true" if value else "false"), None
        s = str(value).strip().lower()
        if s in _TRUE:
            return "true", None
        if s in _FALSE:
            return "false", None
        return None, f"{value!r} is not a boolean"
    if t is FieldType.CURRENCY:
        code = _CURRENCY.get(str(value).strip().upper())
        if code is None and re.fullmatch(r"[A-Za-z]{3}", str(value).strip()):
            code = str(value).strip().upper()
        return (code, None) if code else (None, f"{value!r} is not a currency code")
    if t is FieldType.ENUM:
        want = comparison_key(str(value))
        for ev in spec.enum_values or []:
            if comparison_key(ev) == want:
                return ev, None
        return None, f"{value!r} is not one of {spec.enum_values}"
    s = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()
    if spec.format and _is_regex(spec.format) and not re.fullmatch(spec.format, s):
        return None, f"{s!r} does not match the format {spec.format}"
    return s, None


def _is_regex(fmt: str) -> bool:
    return fmt.startswith("^") or fmt.endswith("$") or "\\" in fmt or "[" in fmt


def comparison_key(text: str) -> str:
    """Loose equality for strings across sources: case, punctuation and spacing do not matter."""
    s = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w]+", "", s, flags=re.UNICODE)


def values_agree(a: str | None, b: str | None, spec: FieldSpec, tolerance: float = 0.005) -> bool:
    """Do two normalised values mean the same thing?"""
    if a is None or b is None:
        return a is b
    if spec.type in (FieldType.NUMBER, FieldType.INTEGER):
        try:
            return abs(Decimal(a) - Decimal(b)) <= Decimal(str(tolerance))
        except InvalidOperation:
            return a == b
    if spec.type is FieldType.STRING:
        return comparison_key(a) == comparison_key(b)
    return a == b


def typed(normalized: str | None, spec: FieldSpec) -> Scalar:
    """Normalised string -> the Python value written to outputs."""
    if normalized is None:
        return None
    if spec.type is FieldType.INTEGER:
        return int(Decimal(normalized))
    if spec.type is FieldType.NUMBER:
        return float(Decimal(normalized))
    if spec.type is FieldType.BOOLEAN:
        return normalized == "true"
    return normalized
