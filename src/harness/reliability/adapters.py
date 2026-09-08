from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from harness.core.events import RunEvent
from harness.core.ports import EventRepository
from harness.reliability.metrics import ReliabilityMetrics


class ObservedEventRepository:
    """Measure durable-event read delay without changing event semantics."""

    def __init__(
        self,
        delegate: EventRepository,
        metrics: ReliabilityMetrics,
        *,
        clock: Callable[[], datetime] | None = None,
        max_seen_events: int = 50_000,
    ) -> None:
        self._delegate = delegate
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seen_order: deque[str] = deque()
        self._seen: set[str] = set()
        self._max_seen_events = max_seen_events
        self._lock = threading.Lock()

    async def append(self, event: RunEvent) -> None:
        await self._delegate.append(event)

    async def latest_sequence(self, tenant_id: str, run_id: str) -> int:
        return await self._delegate.latest_sequence(tenant_id, run_id)

    async def list_after(self, tenant_id: str, run_id: str, after_sequence: int) -> list[RunEvent]:
        events = await self._delegate.list_after(tenant_id, run_id, after_sequence)
        if after_sequence <= 0:
            return events
        now = self._clock()
        for event in events:
            with self._lock:
                if event.event_id in self._seen:
                    continue
                self._seen.add(event.event_id)
                self._seen_order.append(event.event_id)
                while len(self._seen_order) > self._max_seen_events:
                    self._seen.discard(self._seen_order.popleft())
            self._metrics.observe(
                "harness_event_visibility_delay_seconds",
                max(0.0, (now - event.timestamp).total_seconds()),
            )
        return events

    async def latest_for_session_type(
        self, tenant_id: str, session_id: str, event_type: str
    ) -> RunEvent | None:
        return await self._delegate.latest_for_session_type(tenant_id, session_id, event_type)

    async def latest_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...]
    ) -> RunEvent | None:
        return await self._delegate.latest_for_session_types(tenant_id, session_id, event_types)
