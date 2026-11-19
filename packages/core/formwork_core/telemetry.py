"""OpenTelemetry hooks. Every pipeline stage and LLM call opens a span carrying job_id and tenant_id.

Langfuse (and any OTLP backend) can ingest these spans: set OTEL_EXPORTER_OTLP_ENDPOINT and install
`opentelemetry-exporter-otlp-proto-http`. LLM spans use the gen_ai.* semantic attributes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

_tracer = trace.get_tracer("formwork")


def configure_telemetry(
    service_name: str, otlp_endpoint: str | None = None, exporter: SpanExporter | None = None
) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is None and otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found,unused-ignore]
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint.rstrip("/") + "/v1/traces")
        except ImportError:  # exporter is an optional extra
            exporter = None
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    with _tracer.start_as_current_span(name) as sp:
        for k, v in attributes.items():
            if v is not None:
                sp.set_attribute(k, v if isinstance(v, str | bool | int | float) else str(v))
        yield sp
