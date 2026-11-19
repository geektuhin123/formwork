"""Field-path helpers. Spec paths use '[]' ('line_items[].amount'); concrete paths carry an index."""

from __future__ import annotations

import hashlib
import re
import unicodedata

_INDEX = re.compile(r"\[(\d+)\]")


def spec_path(path: str) -> str:
    return _INDEX.sub("[]", path)


def concrete_path(spec: str, index: int) -> str:
    return spec.replace("[]", f"[{index}]", 1)


def array_index(path: str) -> int | None:
    m = _INDEX.search(path)
    return int(m.group(1)) if m else None


def array_root(path: str) -> str | None:
    """'line_items[3].amount' -> 'line_items'; None for scalar paths."""
    i = path.find("[")
    return path[:i] if i >= 0 else None


def leaf_name(path: str) -> str:
    return spec_path(path).split(".")[-1].replace("[]", "")


def normalize_key(value: object) -> str:
    """Trim, case-fold, strip punctuation: 'INV/2026-0042 ' -> 'inv-2026-0042'."""
    s = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    s = re.sub(r"[^\w]+", "-", s, flags=re.UNICODE).strip("-_")
    return s


def record_key(values: list[object]) -> str:
    parts = [normalize_key(v) for v in values if v is not None and str(v).strip()]
    if not parts:
        return ""
    key = "|".join(parts)
    if len(key) > 96:
        return "h_" + hashlib.sha256(key.encode()).hexdigest()[:24]
    return key
