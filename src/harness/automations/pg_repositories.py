"""PostgreSQL repositories for automation tasks and run records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import CursorResult, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from harness.automations.models import (
    AutomationDispatch,
    AutomationRunRecord,
    AutomationStatus,
    AutomationTask,
)
from harness.automations.repositories import DISPATCH_LEASE
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from harness.storage.models import AutomationDispatchRow, AutomationRecordRow, AutomationTaskRow


def _task_payload(task: AutomationTask) -> dict[str, Any]:
    return task.model_dump(mode="json", by_alias=True)


def _task(row: AutomationTaskRow) -> AutomationTask:
    value = AutomationTask.model_validate(row.payload)
    if (
        value.task_id,
        value.tenant_id,
        value.user_id,
        value.status.value,
        value.next_run_at,
        value.revision,
        value.created_at,
        value.updated_at,
    ) != (
        row.task_id,
        row.tenant_id,
        row.user_id,
        row.status,
        row.next_run_at,
        row.revision,
        row.created_at,
        row.updated_at,
    ):
        raise ValueError("Corrupt Automation Task persistence envelope")
    return value


def _record_payload(record: AutomationRunRecord) -> dict[str, Any]:
    return record.model_dump(mode="json", by_alias=True)


def _record(row: AutomationRecordRow) -> AutomationRunRecord:
    return AutomationRunRecord.model_validate(row.payload)


def _rank(tasks: list[AutomationTask]) -> list[AutomationTask]:
    active = [item for item in tasks if item.status is not AutomationStatus.EXPIRED]
    expired = [item for item in tasks if item.status is AutomationStatus.EXPIRED]
    active.sort(key=lambda item: (item.next_run_at or item.created_at, item.task_id))
    expired.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
    return active + expired


class PostgresAutomationTaskRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, task: AutomationTask) -> None:
        async with self._sessions() as session:
            session.add(
                AutomationTaskRow(
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    user_id=task.user_id,
                    status=task.status.value,
                    next_run_at=task.next_run_at,
                    revision=task.revision,
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                    payload=_task_payload(task),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(
                    f"Automation Task already exists: {task.task_id}"
                ) from error

    async def get(self, tenant_id: str, task_id: str) -> AutomationTask:
        async with self._sessions() as session:
            row = await session.get(AutomationTaskRow, task_id)
            if row is None or row.tenant_id != tenant_id:
                raise NotFoundError(f"Automation Task not found: {task_id}")
            return _task(row)

    async def list_for_user(self, tenant_id: str, user_id: str) -> list[AutomationTask]:
        statement = select(AutomationTaskRow).where(
            AutomationTaskRow.tenant_id == tenant_id,
            AutomationTaskRow.user_id == user_id,
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return _rank([_task(row) for row in rows])

    async def replace(self, expected_revision: int, task: AutomationTask) -> None:
        statement = (
            update(AutomationTaskRow)
            .where(
                AutomationTaskRow.task_id == task.task_id,
                AutomationTaskRow.tenant_id == task.tenant_id,
                AutomationTaskRow.revision == expected_revision,
            )
            .values(
                status=task.status.value,
                next_run_at=task.next_run_at,
                revision=task.revision,
                updated_at=task.updated_at,
                payload=_task_payload(task),
            )
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            changed = bool(cast(CursorResult[Any], result).rowcount)
            await (session.commit() if changed else session.rollback())
        if not changed:
            current = await self.get(task.tenant_id, task.task_id)
            raise ConflictError(
                "Automation Task revision changed: "
                f"expected={expected_revision} actual={current.revision}"
            )

    async def delete(self, tenant_id: str, task_id: str) -> None:
        statement = delete(AutomationTaskRow).where(
            AutomationTaskRow.task_id == task_id,
            AutomationTaskRow.tenant_id == tenant_id,
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            changed = bool(cast(CursorResult[Any], result).rowcount)
            await (session.commit() if changed else session.rollback())
        if not changed:
            raise NotFoundError(f"Automation Task not found: {task_id}")

    async def list_due(self, now: datetime, *, limit: int) -> list[AutomationTask]:
        statement = (
            select(AutomationTaskRow)
            .where(
                AutomationTaskRow.status == AutomationStatus.ACTIVE.value,
                AutomationTaskRow.next_run_at.is_not(None),
                AutomationTaskRow.next_run_at <= now,
                ~select(AutomationDispatchRow.dispatch_id).where(
                    AutomationDispatchRow.task_id == AutomationTaskRow.task_id
                ).exists(),
            )
            .order_by(AutomationTaskRow.next_run_at, AutomationTaskRow.task_id)
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_task(row) for row in rows]

    async def claim_due(
        self,
        dispatch: AutomationDispatch,
        *,
        next_run_at: datetime | None,
        status: str,
        last_run_at: datetime,
    ) -> bool:
        task = dispatch.task
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(AutomationTaskRow)
                .where(
                    AutomationTaskRow.task_id == task.task_id,
                    AutomationTaskRow.tenant_id == task.tenant_id,
                    AutomationTaskRow.user_id == task.user_id,
                    AutomationTaskRow.revision == task.revision,
                    AutomationTaskRow.status == AutomationStatus.ACTIVE.value,
                    AutomationTaskRow.next_run_at == dispatch.scheduled_at,
                )
                .with_for_update()
            )
            if row is None:
                return False
            if await session.scalar(select(AutomationDispatchRow.dispatch_id).where(
                AutomationDispatchRow.task_id == task.task_id
            )):
                return False
            updated = _task(row).model_copy(
                update={
                    "next_run_at": next_run_at,
                    "status": AutomationStatus(status),
                    "last_run_at": last_run_at,
                    "updated_at": last_run_at,
                }
            )
            row.status = updated.status.value
            row.next_run_at = updated.next_run_at
            row.updated_at = updated.updated_at
            row.payload = _task_payload(updated)
            session.add(AutomationDispatchRow(
                dispatch_id=dispatch.dispatch_id, task_id=task.task_id,
                scheduled_at=dispatch.scheduled_at, payload=dispatch.model_dump(mode="json"),
            ))
            return True

    async def claim_dispatch(self, now: datetime) -> tuple[AutomationDispatch, str] | None:
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(AutomationDispatchRow)
                .where(or_(
                    AutomationDispatchRow.lease_until.is_(None),
                    AutomationDispatchRow.lease_until <= now,
                ))
                .order_by(AutomationDispatchRow.scheduled_at, AutomationDispatchRow.dispatch_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            token = uuid4().hex
            row.lease_token, row.lease_until = token, now + DISPATCH_LEASE
            return AutomationDispatch.model_validate(row.payload), token

    async def finish_dispatch(self, dispatch_id: str, token: str) -> None:
        async with self._sessions.begin() as session:
            await session.execute(delete(AutomationDispatchRow).where(
                AutomationDispatchRow.dispatch_id == dispatch_id,
                AutomationDispatchRow.lease_token == token,
            ))


class PostgresAutomationRecordRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, record: AutomationRunRecord, *, if_missing: bool = False) -> None:
        async with self._sessions() as session:
            values = dict(
                record_id=record.record_id,
                tenant_id=record.tenant_id,
                task_id=record.task_id,
                user_id=record.user_id,
                status=record.status.value,
                session_id=record.session_id,
                run_id=record.run_id,
                started_at=record.started_at,
                payload=_record_payload(record),
            )
            if if_missing:
                await session.execute(
                    insert(AutomationRecordRow).values(**values)
                    .on_conflict_do_nothing(index_elements=[AutomationRecordRow.record_id])
                )
            else:
                session.add(AutomationRecordRow(**values))
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(
                    f"Automation Run Record already exists: {record.record_id}"
                ) from error

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int
    ) -> list[AutomationRunRecord]:
        statement = (
            select(AutomationRecordRow)
            .where(
                AutomationRecordRow.tenant_id == tenant_id,
                AutomationRecordRow.user_id == user_id,
            )
            .order_by(
                AutomationRecordRow.started_at.desc(),
                AutomationRecordRow.record_id.desc(),
            )
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_record(row) for row in rows]

    async def update_status(
        self, tenant_id: str, record_id: str, **fields: object
    ) -> AutomationRunRecord | None:
        async with self._sessions() as session:
            row = await session.get(AutomationRecordRow, record_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            # model_copy bypasses validation, so raw status strings in
            # ``fields`` must be re-coerced into the status enum.
            updated = AutomationRunRecord.model_validate(
                _record(row).model_copy(update=fields).model_dump(
                    mode="json", by_alias=True
                )
            )
            row.status = updated.status.value
            row.payload = _record_payload(updated)
            await session.commit()
            return updated
