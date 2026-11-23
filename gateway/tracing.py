"""OpenTelemetry tracing for the gateway (Week 6 Day 3).

Off unless OTEL_EXPORTER_OTLP_ENDPOINT is set (the Helm chart sets it when tracing.enabled=true), so local runs,
unit tests and CI work without a trace backend. When on, every request becomes a trace:

  POST /v1/ask                       (FastAPI, automatic)
  ├─ guardrail.input                 (manual: blocked? reason)
  ├─ rag.retrieve                    (manual: top_k, mode, chunks kept / blocked)
  │   └─ POST                        (httpx, automatic: the call to the RAG service, with traceparent header)
  ├─ llm.generate                    (manual: model, tokens, time inside the model)
  │   └─ POST                        (httpx, automatic: the call to Ollama)
  └─ guardrail.output                (manual: citation outcome, repairs, redactions)

Standard OTEL_* variables configure the exporter and sampling (OTEL_SERVICE_NAME, OTEL_TRACES_SAMPLER, ...).
"""

import os

from opentelemetry import trace

tracer = trace.get_tracer("llm-gateway")  # no-op until setup_tracing() installs a provider

EXCLUDED_URLS = "healthz,readyz,metrics,version"  # probes and scrapes would bury the real traces


def setup_tracing(app, version: str, exporter=None) -> bool:
    """Install a tracer provider and auto-instrument FastAPI + httpx.

    `exporter` is for tests (an in-memory exporter, flushed synchronously). Returns True if tracing is on.
    """
    if exporter is None and not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    resource = Resource.create(
        {"service.name": os.getenv("OTEL_SERVICE_NAME", "llm-gateway"), "service.version": version}
    )
    provider = TracerProvider(resource=resource)  # sampler from OTEL_TRACES_SAMPLER(_ARG), default: all
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint from the environment
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS, exclude_spans=["receive", "send"]
    )  # ASGI internals: noise
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    return True


def untraced():
    """Context manager: calls inside it create no spans (used for the readiness check's call to Ollama).

    Excluding /readyz only removes the server span; the outgoing httpx call then had no parent and became a
    one-span "GET" trace of its own, a few per second across pods (found on the cluster, Week 6 Day 3).
    """
    from opentelemetry.instrumentation.utils import suppress_instrumentation

    return suppress_instrumentation()


def current_trace_id() -> str | None:
    """Hex trace id of the active span, or None when tracing is off (put in logs and responses)."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
