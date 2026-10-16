"""Optional OpenTelemetry (CE-15). Activated only when OTEL_EXPORTER_OTLP_ENDPOINT is set
AND the optional ``[otel]`` extra is installed. Spans carry ids/keys/counts only — never
message content or secrets (see redaction). Absent ⇒ no-op tracer."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import Any

from chat_engine.observability.redaction import redact


class _NoopSpan:
    def set_attribute(self, *_: Any) -> None: ...


class Tracer:
    def __init__(self, impl: Any | None = None) -> None:
        self._impl = impl
        self.enabled = impl is not None

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Any]:
        safe = {k: v for k, v in redact(attrs).items() if isinstance(v, str | int | float | bool)}
        if self._impl is None:
            yield _NoopSpan()
            return
        with self._impl.start_as_current_span(name) as s:
            for k, v in safe.items():
                s.set_attribute(f"ce.{k}", v)
            yield s


def build_tracer(env: Mapping[str, str]) -> Tracer:
    endpoint = env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return Tracer(None)
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:  # extra not installed ⇒ honest no-op
        return Tracer(None)
    provider = TracerProvider(resource=Resource.create({"service.name": "chat_engine"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return Tracer(trace.get_tracer("chat_engine"))


__all__ = ["Tracer", "build_tracer"]
