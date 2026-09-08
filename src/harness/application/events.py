"""Ordered event persistence and fan-out."""

from typing import Any, Protocol

from harness.application.types import Clock, IdGenerator
from harness.core.errors import EventSequenceConflictError
from harness.core.events import RunEvent
from harness.core.ports import EventBus, EventRepository


class TraceContext(Protocol):
    def current_trace_id(self) -> str | None: ...

    def current_span_id(self) -> str | None: ...


class EventService:
    def __init__(
        self,
        repository: EventRepository,
        bus: EventBus,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        trace_context: TraceContext | None = None,
    ) -> None:
        self._repository = repository
        self._bus = bus
        self._clock = clock
        self._id_generator = id_generator
        self._trace_context = trace_context

    async def list_after(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
    ) -> list[RunEvent]:
        return await self._repository.list_after(tenant_id, run_id, after_sequence)

    async def latest_for_session_type(
        self,
        tenant_id: str,
        session_id: str,
        event_type: str,
    ) -> RunEvent | None:
        return await self._repository.latest_for_session_type(
            tenant_id,
            session_id,
            event_type,
        )

    async def latest_for_session_types(
        self,
        tenant_id: str,
        session_id: str,
        event_types: tuple[str, ...],
    ) -> RunEvent | None:
        return await self._repository.latest_for_session_types(
            tenant_id,
            session_id,
            event_types,
        )

    async def append(
        self,
        *,
        tenant_id: str,
        run_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        event = RunEvent(
            event_id=self._id_generator("event"),
            run_id=run_id,
            session_id=session_id,
            tenant_id=tenant_id,
            sequence=1,
            type=event_type,
            timestamp=self._clock(),
            payload=payload or {},
            trace_id=(
                self._trace_context.current_trace_id() if self._trace_context is not None else None
            ),
            span_id=(
                self._trace_context.current_span_id() if self._trace_context is not None else None
            ),
        )
        while True:
            sequence = await self._repository.latest_sequence(tenant_id, run_id) + 1
            event = event.model_copy(update={"sequence": sequence})
            try:
                await self._repository.append(event)
            except EventSequenceConflictError:
                continue
            break
        await self._bus.publish(event)
        return event
