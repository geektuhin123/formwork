"""FastAPI application factory."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from formwork_core.config import Settings, get_settings
from formwork_core.db import DbCallSink, app_engine
from formwork_core.db.session import role_bypasses_rls
from formwork_core.errors import FormworkError
from formwork_core.ids import new_id
from formwork_core.logging import bind_context, clear_context, configure_logging, get_logger
from formwork_core.provider import MeteredProvider, ModelProvider, build_provider
from formwork_core.storage import ObjectStore, build_object_store
from formwork_core.telemetry import configure_telemetry
from formwork_pipeline.job import JobRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from formwork_api.auth import RateLimiter
from formwork_api.queue import ArqQueue, InlineQueue, JobQueue
from formwork_api.routes import router

log = get_logger("formwork.api")


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    store: ObjectStore
    provider: ModelProvider
    queue: JobQueue
    runner: JobRunner
    limiter: RateLimiter

    def metered(self, tenant_id: str) -> ModelProvider:
        """Provider for calls outside a job (schema drafting): logged to llm_calls, no job cache."""
        return MeteredProvider(self.provider, DbCallSink(self.engine), tenant_id, None)


def build_state(
    settings: Settings,
    engine: AsyncEngine | None = None,
    store: ObjectStore | None = None,
    provider: ModelProvider | None = None,
    queue: JobQueue | None = None,
) -> AppState:
    engine = engine or app_engine(settings)
    store = store or build_object_store(settings)
    provider = provider or build_provider(settings)
    queue = queue or (InlineQueue(settings) if settings.queue == "inline" else ArqQueue(settings))
    runner = JobRunner(engine, store, provider, settings, notify=queue.enqueue_webhook)
    if isinstance(queue, InlineQueue):
        queue.runner = runner
    return AppState(
        settings, engine, store, provider, queue, runner, RateLimiter(settings.rate_limit_per_minute)
    )


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or (state.settings if state else get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_json, settings.log_level)
        configure_telemetry("formwork-api", settings.otel_exporter_otlp_endpoint)
        app.state.fw = state or build_state(settings)
        fw: AppState = app.state.fw
        try:
            if await role_bypasses_rls(fw.engine):
                msg = "the API database role bypasses row-level security (superuser/owner); use a non-owner role"
                if settings.env == "prod":
                    raise RuntimeError(msg)
                log.warning("rls_bypass", detail=msg)
        except RuntimeError:
            raise
        except Exception as exc:  # database not reachable yet: /healthz will say so
            log.warning("startup_db_check_failed", error=type(exc).__name__)
        log.info("api_started", env=settings.env, provider=fw.provider.name, queue=settings.queue)
        yield
        await fw.queue.close()
        await fw.engine.dispose()

    app = FastAPI(
        title="Formwork API",
        version="0.1.0",
        lifespan=lifespan,
        description="Schema-driven document transformation: a target format plus unstructured sources in, one cited dataset out.",
        docs_url=None if settings.env == "prod" else "/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        rid = request.headers.get("x-request-id", "")[:64] or new_id("req")
        clear_context()
        bind_context(request_id=rid)
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error", path=request.url.path, method=request.method)
            response = JSONResponse(
                {"error": {"code": "internal_error", "message": "internal error", "request_id": rid}},
                status_code=500,
            )
        response.headers["X-Request-Id"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path != "/healthz":
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                ms=int((time.monotonic() - started) * 1000),
            )
        return response

    @app.exception_handler(FormworkError)
    async def formwork_error(request: Request, exc: FormworkError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return JSONResponse(
            {
                "error": {
                    "code": "validation_failed",
                    "message": f"{where}: {first.get('msg', 'invalid request')}".strip(": "),
                }
            },
            status_code=422,
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request) -> JSONResponse:
        try:
            async with request.app.state.fw.engine.connect() as conn:
                await conn.execute(text("select 1"))
            return JSONResponse({"status": "ok"})
        except Exception:
            return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)

    app.include_router(router)
    return app


def app_factory() -> FastAPI:
    """uvicorn formwork_api.app:app_factory --factory"""
    return create_app()
