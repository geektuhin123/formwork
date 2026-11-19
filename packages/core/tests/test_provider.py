import json
from pathlib import Path

import pytest
from formwork_core.config import Settings
from formwork_core.db import DbCallSink, TenantRepo, tenant_tx
from formwork_core.errors import BudgetExceeded, ProviderError
from formwork_core.provider import (
    AnthropicProvider,
    Budget,
    FakeProvider,
    LLMRequest,
    MemorySink,
    MeteredProvider,
    ModelTier,
    ToolSpec,
)

try:  # newer anthropic SDKs ship on httpx2
    import httpx2 as httpx
except ImportError:
    import httpx

CASSETTE = json.loads((Path(__file__).parent / "cassettes" / "anthropic_tool_call.json").read_text())
TOOL = ToolSpec(
    name="classify_document",
    description="classify",
    input_schema={
        "type": "object",
        "properties": {"doc_class": {"type": "string"}, "confidence": {"type": "number"}},
    },
)
REQ = LLMRequest(
    system="sys", prompt='<block id="b1" type="text">\nTAX INVOICE\n</block>', tool=TOOL, purpose="classify"
)


def anthropic_with(handler) -> AnthropicProvider:
    settings = Settings(env="test", anthropic_api_key="sk-test-not-real", llm_max_retries=2)
    return AnthropicProvider(settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_anthropic_provider_replays_cassette_and_uses_configured_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=CASSETTE["response"])

    resp = await anthropic_with(handler).complete(REQ)
    assert resp.output == {"doc_class": "invoice", "confidence": 0.97}
    assert seen["model"] == "claude-haiku-5-5"  # from Settings, not the call site
    assert seen["tool_choice"] == {"type": "tool", "name": "classify_document"}
    assert resp.input_tokens == 812 and resp.cost_usd == pytest.approx((812 * 1.0 + 24 * 5.0) / 1e6)


async def test_anthropic_provider_retries_then_fails(monkeypatch):
    calls = {"n": 0}

    async def nosleep(_):
        return None

    monkeypatch.setattr("formwork_core.provider.asyncio.sleep", nosleep)

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(
                529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
            )
        return httpx.Response(200, json=CASSETTE["response"])

    assert (await anthropic_with(flaky).complete(REQ)).output["doc_class"] == "invoice"
    assert calls["n"] == 3

    def bad(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
        )

    with pytest.raises(ProviderError):
        await anthropic_with(bad).complete(REQ)


async def test_metered_provider_caches_per_job_and_enforces_budget():
    sink = MemorySink()
    fake = FakeProvider(script=[{"doc_class": "invoice", "confidence": 1}])
    budget = Budget(limit_usd=0.000001)
    metered = MeteredProvider(fake, sink, "ten_1", "job_1", budget)
    first = await metered.complete(REQ)
    again = await metered.complete(
        REQ
    )  # identical request in the same job: served from the log, not re-billed
    assert again.cached and again.output == first.output and len(fake.calls) == 1 and len(sink.records) == 1
    with pytest.raises(BudgetExceeded):
        await metered.complete(REQ.model_copy(update={"prompt": "different"}))
    assert REQ.fingerprint("a") != REQ.fingerprint("b")
    assert ModelTier.CHEAP.next_up() is ModelTier.STRONG


@pytest.mark.db
async def test_cassette_call_writes_llm_calls_row(db, tenant):
    provider = anthropic_with(lambda r: httpx.Response(200, json=CASSETTE["response"]))
    metered = MeteredProvider(provider, DbCallSink(db), tenant["id"], None)
    await metered.complete(REQ)
    async with tenant_tx(db, tenant["id"]) as conn:
        rows = (
            await conn.exec_driver_sql("select model, purpose, input_tokens, cost_usd from llm_calls")
        ).all()
    assert (
        len(rows) == 1 and rows[0][0] == "claude-haiku-5-5" and rows[0][1] == "classify" and rows[0][2] == 812
    )
    assert float(rows[0][3]) > 0
    assert TenantRepo(None, tenant["id"]).tenant_id  # type: ignore[arg-type]
