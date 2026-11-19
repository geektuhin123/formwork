"""Engines and the tenant-scoped transaction.

Isolation model: the application connects as a non-owner role (formwork_app). Every tenant table has a
row-level-security policy `tenant_id = current_setting('app.tenant_id')`. `tenant_tx` sets that GUC for the
transaction; with no tenant set, queries see nothing. Repositories also filter by tenant_id explicitly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from formwork_core.config import Settings


def app_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_size=10, max_overflow=10, pool_pre_ping=True)


def admin_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_admin_url or settings.database_url, pool_size=2, pool_pre_ping=True
    )


@asynccontextmanager
async def tenant_tx(engine: AsyncEngine, tenant_id: str) -> AsyncIterator[AsyncConnection]:
    if not tenant_id:
        raise ValueError("tenant_tx requires a tenant_id")
    async with engine.begin() as conn:
        await conn.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        yield conn


async def role_bypasses_rls(engine: AsyncEngine) -> bool:
    """True when the connected role would ignore tenant policies (superuser / BYPASSRLS / table owner)."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "select r.rolsuper or r.rolbypassrls or exists (select 1 from pg_tables t "
                    "where t.schemaname = 'public' and t.tablename = 'jobs' and t.tableowner = current_user) "
                    "from pg_roles r where r.rolname = current_user"
                )
            )
        ).first()
        return bool(row and row[0])
