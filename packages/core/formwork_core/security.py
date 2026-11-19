"""API-key hashing and webhook signing. Keys are high-entropy random strings, so a keyed hash (HMAC-SHA256 with a
server-side pepper) is the right primitive: fast to verify, useless to an attacker who only has the database."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

KEY_PREFIX = "fw"


def generate_api_key(env: str = "live") -> str:
    return f"{KEY_PREFIX}_{env}_{secrets.token_urlsafe(32)}"


def hash_api_key(key: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), key.encode(), hashlib.sha256).hexdigest()


def generate_webhook_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign_webhook(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """Header value: 't=<unix>,v1=<hex hmac-sha256 of "<t>.<body>">'."""
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify_webhook(
    secret: str, body: bytes, header: str, tolerance_s: int = 300, now: int | None = None
) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["t"])
        given = parts["v1"]
    except (KeyError, ValueError):
        return False
    current = int(time.time()) if now is None else now
    if abs(current - ts) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)
