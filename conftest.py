"""Repo-wide fixtures: a temporary Postgres (migrated), engines, and a tenant."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

ROOT = Path(__file__).parent
os.environ.setdefault("FORMWORK_REPO_ROOT", str(ROOT))
os.environ.setdefault("FORMWORK_ENV", "test")
os.environ.pop("ANTHROPIC_API_KEY", None) if os.environ.get("FORMWORK_TESTS_LIVE") != "1" else None


@pytest.fixture(scope="session")
def pg() -> Iterator[Any]:
    from formwork_testkit.pg import migrate, start_postgres

    server = start_postgres()
    if server is None:
        pytest.skip("no Postgres available (install postgresql or set FORMWORK_TEST_DATABASE_ADMIN_URL)")
    migrate(server.admin_url, repo_root=str(ROOT))
    yield server
    server.stop()


@pytest.fixture
async def admin_db(pg: Any) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg.admin_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE tenants CASCADE"))
        await conn.execute(text("TRUNCATE mapping_plans, llm_calls, eval_cases, webhook_deliveries"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def db(pg: Any, admin_db: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg.app_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def tenant(admin_db: AsyncEngine) -> dict[str, Any]:
    from formwork_core.db.repo import create_tenant

    return await create_tenant(admin_db, "Test Tenant")
