"""ModelProvider: the only door to an LLM.

Call sites pass a *tier* (cheap | strong), never a model id; ids and prices come from Settings.
`MeteredProvider` wraps any provider with: a job-scoped response cache (so a restarted stage never pays for
the same call twice), per-job budget enforcement, cost logging to a `CallSink` (the llm_calls table) and a span.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from formwork_core.config import Settings
from formwork_core.errors import BudgetExceeded, ProviderError
from formwork_core.ids import new_id
from formwork_core.logging import get_logger
from formwork_core.telemetry import span

log = get_logger("formwork.provider")


class ModelTier(StrEnum):
    CHEAP = "cheap"
    STRONG = "strong"

    def next_up(self) -> ModelTier:
        return ModelTier.STRONG


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class LLMRequest(BaseModel):
    tier: ModelTier = ModelTier.CHEAP
    system: str
    prompt: str
    images: list[bytes] = Field(default_factory=list)  # PNG bytes
    tool: ToolSpec
    purpose: str  # "classify", "extract:text", "mapping_plan", ...
    max_tokens: int = 4096

    def fingerprint(self, model: str) -> str:
        h = hashlib.sha256()
        payload = [model, self.system, self.prompt, self.tool.model_dump(), self.purpose, self.max_tokens]
        h.update(json.dumps(payload, sort_keys=True).encode())
        for img in self.images:
            h.update(hashlib.sha256(img).digest())
        return h.hexdigest()


class LLMResponse(BaseModel):
    output: dict[str, Any]
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    cached: bool = False


class ModelProvider(Protocol):
    name: str

    def model_for(self, tier: ModelTier) -> str: ...

    async def complete(self, request: LLMRequest) -> LLMResponse: ...


def cost_usd(settings: Settings, tier: ModelTier, input_tokens: int, output_tokens: int) -> float:
    pin, pout = settings.price(tier.value)
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 6)


# ---------------------------------------------------------------- Anthropic
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings: Settings, http_client: Any | None = None) -> None:
        import anthropic

        if settings.anthropic_api_key is None:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self._settings = settings
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_retries=0,  # retries are handled here so they are logged and bounded
            timeout=settings.llm_timeout_s,
            http_client=http_client,
        )

    def model_for(self, tier: ModelTier) -> str:
        return self._settings.model_strong if tier is ModelTier.STRONG else self._settings.model_cheap

    def _retryable(self, exc: Exception) -> bool:
        a = self._anthropic
        if isinstance(
            exc, a.RateLimitError | a.APIConnectionError | a.APITimeoutError | a.InternalServerError
        ):
            return True
        return isinstance(exc, a.APIStatusError) and exc.status_code in (408, 409, 529)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self.model_for(request.tier)
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(img).decode(),
                },
            }
            for img in request.images
        ]
        content.append({"type": "text", "text": request.prompt})
        started = time.monotonic()
        attempt = 0
        create: Any = (
            self._client.messages.create
        )  # params are plain dicts; the SDK's TypedDict overloads add nothing here
        while True:
            try:
                msg = await create(
                    model=model,
                    max_tokens=request.max_tokens,
                    # The system prompt + tool schema repeat across every chunk of a job: cache them.
                    system=[{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": content}],
                    tools=[request.tool.model_dump()],
                    tool_choice={"type": "tool", "name": request.tool.name},
                )
                break
            except Exception as exc:
                attempt += 1
                if attempt > self._settings.llm_max_retries or not self._retryable(exc):
                    raise ProviderError(f"anthropic call failed after {attempt} attempt(s): {exc}") from exc
                delay = min(30.0, 0.5 * 2**attempt) * (0.5 + random.random())
                log.warning("llm_retry", attempt=attempt, delay_s=round(delay, 2), error=type(exc).__name__)
                await asyncio.sleep(delay)
        output: dict[str, Any] | None = None
        for part in msg.content:
            if part.type == "tool_use" and part.name == request.tool.name:
                output = dict(part.input) if isinstance(part.input, dict) else None
        if output is None:
            raise ProviderError("model did not return the requested tool call")
        usage = msg.usage
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        billable_in = usage.input_tokens + int(cache_write * 1.25) + int(cache_read * 0.1)
        return LLMResponse(
            output=output,
            model=model,
            input_tokens=usage.input_tokens + cache_write + cache_read,
            output_tokens=usage.output_tokens,
            cost_usd=cost_usd(self._settings, request.tier, billable_in, usage.output_tokens),
            latency_ms=int((time.monotonic() - started) * 1000),
        )


# ---------------------------------------------------------------- Fake
Responder = Callable[[LLMRequest], dict[str, Any]]


class FakeProvider:
    """Deterministic provider for tests and offline evals.

    Resolution order per call: scripted outputs (FIFO) -> cassette entry (by request fingerprint) -> responder.
    The default responder is the rule-based `HeuristicModel`: it reads the same prompt a real model would see.
    It is a stand-in for plumbing tests, not a measure of model quality.
    """

    name = "fake"

    def __init__(
        self,
        responder: Responder | None = None,
        script: list[dict[str, Any]] | None = None,
        cassette: str | Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._script = list(script or [])
        self._cassette: dict[str, dict[str, Any]] = {}
        if cassette is not None:
            self._cassette = json.loads(Path(cassette).read_text())
        if responder is None:
            from formwork_core.heuristic import HeuristicModel

            responder = HeuristicModel()
        self._responder = responder
        self.calls: list[LLMRequest] = []

    def model_for(self, tier: ModelTier) -> str:
        return f"fake-{tier.value}"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        key = request.fingerprint(self.model_for(request.tier))
        if self._script:
            output = self._script.pop(0)
        elif key in self._cassette:
            output = self._cassette[key]
        else:
            output = self._responder(request)
        # Token estimate (~4 chars/token, ~1.2k tokens per image) so offline cost numbers are plausible.
        tin = (len(request.system) + len(request.prompt) + len(json.dumps(request.tool.input_schema))) // 4
        tin += 1200 * len(request.images)
        tout = len(json.dumps(output)) // 4
        return LLMResponse(
            output=output,
            model=self.model_for(request.tier),
            input_tokens=tin,
            output_tokens=tout,
            cost_usd=cost_usd(self._settings, request.tier, tin, tout),
            latency_ms=1,
        )


def build_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "anthropic" or (settings.model_provider == "auto" and settings.live_models):
        return AnthropicProvider(settings)
    return FakeProvider(settings=settings)


# ---------------------------------------------------------------- metering
class LLMCallRecord(BaseModel):
    id: str
    tenant_id: str
    job_id: str | None
    model: str
    purpose: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    request_hash: str
    response: dict[str, Any] | None = None
    created_at: datetime


class CallSink(Protocol):
    async def record(self, rec: LLMCallRecord) -> None: ...

    async def lookup(
        self, tenant_id: str, job_id: str | None, request_hash: str
    ) -> dict[str, Any] | None: ...


class MemorySink:
    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    async def record(self, rec: LLMCallRecord) -> None:
        self.records.append(rec)

    async def lookup(self, tenant_id: str, job_id: str | None, request_hash: str) -> dict[str, Any] | None:
        if job_id is None:
            return None
        for r in self.records:
            if (r.tenant_id, r.job_id, r.request_hash) == (tenant_id, job_id, request_hash):
                return r.response
        return None


class Budget:
    """Per-job spend limit. `check()` raises before a call that would start over budget."""

    def __init__(self, limit_usd: float | None, spent_usd: float = 0.0) -> None:
        self.limit_usd = limit_usd
        self.spent_usd = spent_usd

    @property
    def exhausted(self) -> bool:
        return self.limit_usd is not None and self.spent_usd >= self.limit_usd

    def check(self) -> None:
        if self.exhausted:
            raise BudgetExceeded(f"job budget of ${self.limit_usd:.2f} reached (spent ${self.spent_usd:.4f})")

    def add(self, cost: float) -> None:
        self.spent_usd += cost


class MeteredProvider:
    def __init__(
        self,
        inner: ModelProvider,
        sink: CallSink,
        tenant_id: str,
        job_id: str | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.inner = inner
        self.sink = sink
        self.tenant_id = tenant_id
        self.job_id = job_id
        self.budget = budget or Budget(None)
        self.name = inner.name

    def model_for(self, tier: ModelTier) -> str:
        return self.inner.model_for(tier)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        model = self.model_for(request.tier)
        request_hash = request.fingerprint(model)
        cached = await self.sink.lookup(self.tenant_id, self.job_id, request_hash)
        if cached is not None:
            return LLMResponse(output=cached, model=model, cached=True)
        self.budget.check()
        with span(
            "llm.call",
            **{"gen_ai.request.model": model, "formwork.purpose": request.purpose},
            tenant_id=self.tenant_id,
            job_id=self.job_id,
        ) as sp:
            resp = await self.inner.complete(request)
            sp.set_attribute("gen_ai.usage.input_tokens", resp.input_tokens)
            sp.set_attribute("gen_ai.usage.output_tokens", resp.output_tokens)
            sp.set_attribute("formwork.cost_usd", resp.cost_usd)
        self.budget.add(resp.cost_usd)
        await self.sink.record(
            LLMCallRecord(
                id=new_id("llm"),
                tenant_id=self.tenant_id,
                job_id=self.job_id,
                model=resp.model,
                purpose=request.purpose,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                cost_usd=resp.cost_usd,
                latency_ms=resp.latency_ms,
                request_hash=request_hash,
                response=resp.output,
                created_at=datetime.now(UTC),
            )
        )
        return resp
