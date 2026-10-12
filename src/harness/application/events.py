"""Ordered event persistence and fan-out."""

from datetime import datetime
from typing import Any, Protocol

from harness.application.types import Clock, IdGenerator
from harness.core.errors import ConflictError, EventSequenceConflictError
from harness.core.events import RunEvent
from harness.core.ports import EventBus, EventRepository


class TraceContext(Protocol):
    def current_trace_id(self) -> str | None: ...

    def current_span_id(self) -> str | None: ...


class EventService:
    async def append_once(
        self,
        *,
        event_id: str,
        tenant_id: str,
        run_id: str,
        session_id: str,
        event_type: str,
        timestamp: str,
        payload: dict[str, Any],
    ) -> RunEvent:
        """Drain a transactional outbox notice with a stable event identity."""
        while True:
            existing = next(
                (
                    e
                    for e in await self._repository.list_after(
                        tenant_id,
                        run_id,
                        0,
                        types=(event_type,),
                    )
                    if e.event_id == event_id
                ),
                None,
            )
            if existing is not None:
                if existing.payload != payload or existing.session_id != session_id:
                    raise ConflictError("outbox event identity has different content")
                # Persistence may have succeeded before fan-out failed. Retry delivery
                # with the same identity so subscribers can safely deduplicate it.
                await self._bus.publish(existing)
                return existing
            event = RunEvent(
                event_id=event_id,
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=session_id,
                sequence=await self._repository.latest_sequence(tenant_id, run_id) + 1,
                type=event_type,
                timestamp=datetime.fromisoformat(timestamp),
                payload=payload,
            )
            try:
                await self._repository.append(event)
            except EventSequenceConflictError:
                continue
            except ConflictError:
                # A concurrent drain can win this identity between the lookup
                # and sequence allocation. Reload its canonical sequence; a
                # collision outside this run remains an actual conflict.
                if any(
                    e.event_id == event_id
                    for e in await self._repository.list_after(
                        tenant_id, run_id, 0, types=(event_type,)
                    )
                ):
                    continue
                raise
            await self._bus.publish(event)
            return event

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
        return await self._repository.list_after(tenant_id, run_id, after_sequence, types=types)

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
        self,
        tenant_id: str,
        session_id: str,
        event_types: tuple[str, ...],
        *,
        limit: int = 20,
        before: RunEvent | None = None,
        exclude_run_id: str | None = None,
    ) -> list[RunEvent]:
        return await self._repository.recent_for_session_types(
            tenant_id,
            session_id,
            event_types,
            limit=limit,
            before=before,
            exclude_run_id=exclude_run_id,
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
