import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.adapters.memory import InMemoryEventBus, InMemoryEventRepository
from harness.application.events import EventService
from harness.config import Settings
from harness.core.events import RunEvent
from harness.observability.provider import build_observability

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _ids() -> Callable[[str], str]:
    count = 0

    def generate(prefix: str) -> str:
        nonlocal count
        count += 1
        return f"{prefix}-{count}"

    return generate


class RacingEventRepository:
    """Make the first two sequence reads observe the same empty stream."""

    def __init__(self) -> None:
        self._repository = InMemoryEventRepository()
        self._readers = 0
        self._both_reading = asyncio.Event()

    async def append(self, event: RunEvent) -> None:
        await self._repository.append(event)

    async def latest_sequence(self, tenant_id: str, run_id: str) -> int:
        if self._readers < 2:
            self._readers += 1
            if self._readers == 2:
                self._both_reading.set()
            await self._both_reading.wait()
            return 0
        return await self._repository.latest_sequence(tenant_id, run_id)

    async def list_after(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
    ) -> list[RunEvent]:
        return await self._repository.list_after(tenant_id, run_id, after_sequence)

    async def latest_for_session_type(
        self, tenant_id: str, session_id: str, event_type: str
    ) -> RunEvent | None:
        return await self._repository.latest_for_session_type(tenant_id, session_id, event_type)

    async def latest_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...]
    ) -> RunEvent | None:
        return await self._repository.latest_for_session_types(tenant_id, session_id, event_types)


@pytest.mark.asyncio
async def test_concurrent_appends_retry_sequence_conflicts_in_order() -> None:
    repository = RacingEventRepository()
    service = EventService(
        repository,
        InMemoryEventBus(),
        clock=lambda: NOW,
        id_generator=_ids(),
    )

    emitted = await asyncio.gather(
        service.append(
            tenant_id="tenant-a",
            run_id="run-1",
            session_id="session-1",
            event_type="tool.result",
        ),
        service.append(
            tenant_id="tenant-a",
            run_id="run-1",
            session_id="session-1",
            event_type="tool.request",
        ),
    )

    stored = await repository.list_after("tenant-a", "run-1", 0)
    assert sorted(event.sequence for event in emitted) == [1, 2]
    assert [event.sequence for event in stored] == [1, 2]


@pytest.mark.asyncio
async def test_events_keep_the_active_trace_and_span_for_replay_correlation() -> None:
    repository = InMemoryEventRepository()
    exporter = InMemorySpanExporter()
    observability = build_observability(
        Settings(otel_enabled=True, otlp_endpoint="http://unused/v1/traces"),
        exporter=exporter,
        processor_factory=SimpleSpanProcessor,
    )
    service = EventService(
        repository,
        InMemoryEventBus(),
        clock=lambda: NOW,
        id_generator=_ids(),
        trace_context=observability,
    )

    with observability.span("harness.worker.stage"):
        expected_trace_id = observability.current_trace_id()
        expected_span_id = observability.current_span_id()
        emitted = await service.append(
            tenant_id="tenant-a",
            run_id="run-1",
            session_id="session-1",
            event_type="tool.request",
        )

    assert emitted.trace_id == expected_trace_id
    assert emitted.span_id == expected_span_id
    assert expected_trace_id is not None
    assert expected_span_id is not None
