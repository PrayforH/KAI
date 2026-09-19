"""Keep committed tests deterministic when a developer has a real local .env."""

from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.observability.provider import Observability


class SpanRecorder:
    """A real tracer whose finished spans a test can read back.

    Tracing is asserted through the exporter's view rather than through an
    emitter the test expected to be called: the attribute allowlist and the
    redaction pass both sit between an emitter and the backend, so a test that
    only watched the emitter would pass while the attribute never arrived.
    """

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.observability = Observability(
            enabled=True,
            tracer=provider.get_tracer("test"),
            exporter=None,
            content_capture="redacted",
        )

    def spans(self) -> list[ReadableSpan]:
        return list(self.exporter.get_finished_spans())

    def span(self, name: str) -> ReadableSpan:
        found = [span for span in self.spans() if span.name == name]
        assert found, f"no {name!r} span; recorded {[span.name for span in self.spans()]}"
        return found[-1]

    def attributes(self, name: str) -> dict[str, Any]:
        return dict(self.span(name).attributes or {})


@pytest.fixture
def span_recorder() -> SpanRecorder:
    return SpanRecorder()


@pytest.fixture(autouse=True)
def deterministic_harness_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_RUNTIME", "fake")
    monkeypatch.setenv("HARNESS_OTEL_ENABLED", "false")
    monkeypatch.setenv("HARNESS_OTEL_SERVICE_NAME", "claude-agent-harness")
