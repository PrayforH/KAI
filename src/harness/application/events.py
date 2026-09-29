"""Ordered event persistence and fan-out."""

import logging
from typing import Any, Protocol

from harness.application.types import Clock, IdGenerator
from harness.core.errors import EventSequenceConflictError
from harness.core.events import RunEvent
from harness.core.ports import EventBus, EventRepository

logger = logging.getLogger(__name__)


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
        *,
        types: tuple[str, ...] | None = None,
    ) -> list[RunEvent]:
        return await self._repository.list_after(
            tenant_id, run_id, after_sequence, types=types
        )

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

    async def recent_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...],
        *, limit: int = 20, before: RunEvent | None = None,
        exclude_run_id: str | None = None,
    ) -> list[RunEvent]:
        return await self._repository.recent_for_session_types(
            tenant_id, session_id, event_types, limit=limit, before=before,
            exclude_run_id=exclude_run_id,
        )

    def new_event(
        self,
        *,
        tenant_id: str,
        run_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        sequence: int = 1,
    ) -> RunEvent:
        """Build an event for a caller that persists it through its own unit of work.

        The acceptance transaction is what commits a new Run's first event, so
        it needs the same trace and clock stamping ``append`` applies rather
        than a second, drifting construction.
        """

        return RunEvent(
            event_id=self._id_generator("event"),
            run_id=run_id,
            session_id=session_id,
            tenant_id=tenant_id,
            sequence=sequence,
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

    async def notify(self, event: RunEvent) -> None:
        """Fan an already-durable event out transiently.

        Readers re-read PostgreSQL, so a failed publish costs latency and
        nothing else; it must never turn a committed fact into an error for the
        caller that committed it.
        """

        try:
            await self._bus.publish(event)
        except Exception:
            logger.warning(
                "event bus publish failed; durable polling still serves readers",
                extra={
                    "tenant_id": event.tenant_id,
                    "run_id": event.run_id,
                    "event_type": event.type,
                },
                exc_info=True,
            )

    async def append_event(self, event: RunEvent) -> RunEvent:
        """Persist a caller-built event at its next sequence, then fan it out."""

        while True:
            sequence = await self._repository.latest_sequence(
                event.tenant_id, event.run_id
            ) + 1
            event = event.model_copy(update={"sequence": sequence})
            try:
                await self._repository.append(event)
            except EventSequenceConflictError:
                continue
            break
        await self._bus.publish(event)
        return event

    async def append(
        self,
        *,
        tenant_id: str,
        run_id: str,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RunEvent:
        return await self.append_event(
            self.new_event(
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
            )
        )
