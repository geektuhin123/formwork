"""API-key authentication and a per-key rate limit."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request
from formwork_core.db.repo import lookup_api_key
from formwork_core.errors import FormworkError, Unauthorized
from formwork_core.logging import bind_context
from formwork_core.security import KEY_PREFIX, hash_api_key


class RateLimited(FormworkError):
    code = "rate_limited"
    http_status = 429


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    key_id: str


class RateLimiter:
    """Sliding one-minute window per API key, in process. Put a shared limiter (gateway/Redis) in front for multi-replica."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def check(self, key_id: str) -> None:
        now = time.monotonic()
        hits = self._hits.setdefault(key_id, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            raise RateLimited("rate limit exceeded; retry shortly")
        hits.append(now)


async def authenticate(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX + "_") or len(token) > 200:
        raise Unauthorized("missing or malformed API key (use 'Authorization: Bearer fw_...')")
    state = request.app.state.fw
    found = await lookup_api_key(
        state.engine, hash_api_key(token, state.settings.api_key_pepper.get_secret_value())
    )
    if found is None:
        raise Unauthorized("invalid or revoked API key")
    key_id, tenant_id = found
    state.limiter.check(key_id)
    bind_context(tenant_id=tenant_id)
    return Principal(tenant_id=tenant_id, key_id=key_id)
