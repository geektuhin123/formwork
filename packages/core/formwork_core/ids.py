"""Prefixed, sortable-ish identifiers: 'job_01hf...'."""

from __future__ import annotations

import hashlib
import secrets
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    return f"{prefix}_{_b32(int(time.time() * 1000), 10)}{_b32(secrets.randbits(60), 12)}"


def stable_id(prefix: str, *parts: object) -> str:
    """Deterministic id: the same inputs always give the same id (used for blocks so re-parsing is idempotent)."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:20]
    return f"{prefix}_{h}"
