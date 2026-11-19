import httpx
import pytest
from formwork_api import build_state, create_app
from formwork_api.queue import InlineQueue
from formwork_core.config import Settings
from formwork_core.db.repo import create_api_key
from formwork_core.provider import FakeProvider
from formwork_core.storage import MemoryObjectStore


@pytest.fixture
def settings(pg) -> Settings:
    return Settings(
        env="test",
        database_url=pg.app_url,
        queue="inline",
        api_key_pepper="test-pepper",
        webhook_allow_private_hosts=True,
        rate_limit_per_minute=10_000,
    )


@pytest.fixture
async def api(db, admin_db, tenant, settings):
    """(client, state, tenant): the real app over ASGI with a real Postgres, inline queue and FakeProvider."""
    state = build_state(
        settings, engine=db, store=MemoryObjectStore(), provider=FakeProvider(), queue=InlineQueue(settings)
    )
    app = create_app(settings, state)
    key = await create_api_key(admin_db, tenant["id"], "test-pepper")
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://api", headers={"Authorization": f"Bearer {key}"}
        ) as client:
            client.fw_state = state  # type: ignore[attr-defined]
            client.fw_tenant = tenant  # type: ignore[attr-defined]
            yield client
