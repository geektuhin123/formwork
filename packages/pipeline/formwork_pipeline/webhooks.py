"""Webhook delivery: signed with HMAC-SHA256 (tenant secret), SSRF-guarded, retried by the caller."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from formwork_core.config import Settings
from formwork_core.db import TenantRepo, tenant_tx
from formwork_core.errors import ValidationFailed
from formwork_core.logging import get_logger
from formwork_core.security import sign_webhook
from sqlalchemy.ext.asyncio import AsyncEngine

log = get_logger("formwork.webhooks")
SIGNATURE_HEADER = "X-Formwork-Signature"


def validate_webhook_url(url: str, settings: Settings, resolve: bool = False) -> str:
    """Reject anything that is not http(s), and (unless explicitly allowed) anything pointing inside the network."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValidationFailed("webhook_url must be an http(s) URL")
    if settings.env == "prod" and parsed.scheme != "https":
        raise ValidationFailed("webhook_url must use https")
    if settings.webhook_allow_private_hosts:
        return url
    host = parsed.hostname
    addresses: list[str] = []
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
            raise ValidationFailed("webhook_url must not point to a private host") from None
        if resolve:
            try:
                addresses = list({str(i[4][0]) for i in socket.getaddrinfo(host, None)})
            except OSError:
                raise ValidationFailed("webhook_url host does not resolve") from None
    for a in addresses:
        ip = ipaddress.ip_address(a)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValidationFailed("webhook_url must not point to a private address")
    return url


async def deliver(
    engine: AsyncEngine,
    settings: Settings,
    tenant_id: str,
    delivery_id: str,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """One delivery attempt. Returns True when the receiver answered 2xx (or the delivery is already final)."""
    async with tenant_tx(engine, tenant_id) as conn:
        repo = TenantRepo(conn, tenant_id)
        row = await repo.get_delivery(delivery_id)
        secret = (await repo.tenant())["webhook_secret"] or ""
    if row["status"] != "pending":
        return True
    body = json.dumps(row["payload"], separators=(",", ":"), sort_keys=True).encode()
    attempts = int(row["attempts"] or 0) + 1
    error: str | None = None
    try:
        await asyncio.to_thread(validate_webhook_url, row["url"], settings, True)
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign_webhook(secret, body),
            "X-Formwork-Delivery": delivery_id,
            "User-Agent": "formwork-webhooks/1",
        }
        own = client is None
        http = client or httpx.AsyncClient(timeout=settings.webhook_timeout_s, follow_redirects=False)
        try:
            resp = await http.post(row["url"], content=body, headers=headers)
        finally:
            if own:
                await http.aclose()
        if not 200 <= resp.status_code < 300:
            error = f"HTTP {resp.status_code}"
    except (httpx.HTTPError, ValidationFailed, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    status = (
        "delivered"
        if error is None
        else ("failed" if attempts >= settings.webhook_max_attempts else "pending")
    )
    async with tenant_tx(engine, tenant_id) as conn:
        await TenantRepo(conn, tenant_id).mark_delivery(delivery_id, status, attempts, error)
    log.info("webhook_attempt", delivery_id=delivery_id, status=status, attempts=attempts, error=error)
    return error is None or status == "failed"


def status_payload(job: dict[str, Any], previous: str, timestamp: str) -> dict[str, Any]:
    return {
        "event": "job.status_changed",
        "job_id": job["id"],
        "status": job["status"],
        "previous_status": previous,
        "cost_usd": float(job.get("cost_usd") or 0),
        "stats": job.get("stats") or {},
        "error": job.get("error"),
        "timestamp": timestamp,
    }
