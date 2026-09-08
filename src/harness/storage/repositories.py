"""PostgreSQL implementations of authoritative Run and Event ports."""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, EventSequenceConflictError, NotFoundError
from harness.core.events import RunEvent
from harness.core.models import Run, RunStatus
from harness.storage.database import SessionFactory
from harness.storage.models import EventRow, RunRow


class PostgresRunRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, run: Run) -> None:
        async with self._sessions() as session:
            session.add(
                RunRow(
                    tenant_id=run.tenant_id,
                    run_id=run.run_id,
                    session_id=run.session_id,
                    idempotency_key=run.idempotency_key,
                    status=run.status.value,
                    fencing_token=run.fencing_token,
                    updated_at=run.updated_at,
                    payload=run.model_dump(mode="json"),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"run already exists: {run.run_id}") from error

    async def get(self, tenant_id: str, run_id: str) -> Run:
        async with self._sessions() as session:
            row = await session.get(RunRow, (tenant_id, run_id))
            if row is None:
                raise NotFoundError(f"run not found: {run_id}")
            return Run.model_validate(row.payload)

    async def find_by_idempotency_key(
        self, tenant_id: str, session_id: str, idempotency_key: str
    ) -> Run | None:
        statement = select(RunRow).where(
            RunRow.tenant_id == tenant_id,
            RunRow.session_id == session_id,
            RunRow.idempotency_key == idempotency_key,
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return None if row is None else Run.model_validate(row.payload)

    async def compare_and_set(self, expected_status: RunStatus, updated: Run) -> bool:
        statement = (
            update(RunRow)
            .where(
                RunRow.tenant_id == updated.tenant_id,
                RunRow.run_id == updated.run_id,
                RunRow.status == expected_status.value,
                RunRow.fencing_token == updated.fencing_token - 1,
            )
            .values(
                status=updated.status.value,
                fencing_token=updated.fencing_token,
                updated_at=updated.updated_at,
                payload=updated.model_dump(mode="json"),
            )
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            cursor = cast(CursorResult[Any], result)
            return bool(cursor.rowcount)

    async def list_for_sessions(
        self, tenant_id: str, session_ids: list[str], *, limit: int
    ) -> list[Run]:
        if not session_ids:
            return []
        statement = select(RunRow.payload).where(
            RunRow.tenant_id == tenant_id,
            RunRow.session_id.in_(session_ids),
        ).order_by(
            RunRow.updated_at.desc(),
            RunRow.run_id.desc(),
        ).limit(limit)
        async with self._sessions() as session:
            payloads = (await session.execute(statement)).scalars().all()
            return [Run.model_validate(payload) for payload in payloads]

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[Run]:
        statement = (
            select(RunRow.payload)
            .where(RunRow.tenant_id == tenant_id)
            .order_by(RunRow.updated_at.desc(), RunRow.run_id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            payloads = (await session.scalars(statement)).all()
            return [Run.model_validate(payload) for payload in payloads]

    async def list_stale(
        self,
        statuses: frozenset[RunStatus],
        updated_at_or_before: datetime,
        *,
        limit: int,
    ) -> list[Run]:
        statement = (
            select(RunRow.payload)
            .where(
                RunRow.status.in_(tuple(item.value for item in statuses)),
                RunRow.updated_at <= updated_at_or_before,
            )
            .order_by(RunRow.updated_at, RunRow.run_id)
            .limit(limit)
        )
        async with self._sessions() as session:
            payloads = (await session.scalars(statement)).all()
            return [Run.model_validate(payload) for payload in payloads]


class PostgresEventRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def append(self, event: RunEvent) -> None:
        async with self._sessions() as session:
            existing = await session.get(EventRow, event.event_id)
            if existing is not None:
                if RunEvent.model_validate(existing.payload) != event:
                    raise ConflictError(
                        f"event id already contains different data: {event.event_id}"
                    )
                return
            session.add(
                EventRow(
                    event_id=event.event_id,
                    tenant_id=event.tenant_id,
                    run_id=event.run_id,
                    sequence=event.sequence,
                    timestamp=event.timestamp,
                    payload=event.model_dump(mode="json"),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise EventSequenceConflictError(
                    f"event sequence already exists: {event.sequence}"
                ) from error

    async def latest_sequence(self, tenant_id: str, run_id: str) -> int:
        statement = select(func.max(EventRow.sequence)).where(
            EventRow.tenant_id == tenant_id,
            EventRow.run_id == run_id,
        )
        async with self._sessions() as session:
            value = await session.scalar(statement)
        return int(value or 0)

    async def list_after(self, tenant_id: str, run_id: str, after_sequence: int) -> list[RunEvent]:
        statement = (
            select(EventRow)
            .where(
                EventRow.tenant_id == tenant_id,
                EventRow.run_id == run_id,
                EventRow.sequence > after_sequence,
            )
            .order_by(EventRow.sequence)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [RunEvent.model_validate(row.payload) for row in rows]

    async def latest_for_session_type(
        self, tenant_id: str, session_id: str, event_type: str
    ) -> RunEvent | None:
        return await self.latest_for_session_types(tenant_id, session_id, (event_type,))

    async def latest_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...]
    ) -> RunEvent | None:
        if not event_types:
            return None
        statement = (
            select(EventRow)
            .where(
                EventRow.tenant_id == tenant_id,
                EventRow.payload["session_id"].as_string() == session_id,
                EventRow.payload["type"].as_string().in_(event_types),
            )
            .order_by(EventRow.timestamp.desc(), EventRow.run_id.desc(), EventRow.sequence.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return None if row is None else RunEvent.model_validate(row.payload)
