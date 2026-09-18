"""PostgreSQL repositories for automation tasks and run records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError

from harness.automations.models import (
    AutomationRunRecord,
    AutomationStatus,
    AutomationTask,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from harness.storage.models import AutomationRecordRow, AutomationTaskRow


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
            )
            .order_by(AutomationTaskRow.next_run_at, AutomationTaskRow.task_id)
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_task(row) for row in rows]

    async def claim_due(
        self,
        task_id: str,
        *,
        expected_next_run_at: datetime,
        next_run_at: datetime | None,
        status: str,
        last_run_at: datetime,
    ) -> bool:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AutomationTaskRow)
                .where(AutomationTaskRow.task_id == task_id)
                .with_for_update()
            )
            if row is None:
                return False
            current = _task(row)
            if current.next_run_at != expected_next_run_at:
                await session.rollback()
                return False
            updated = current.model_copy(
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
            await session.commit()
            return True


class PostgresAutomationRecordRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, record: AutomationRunRecord) -> None:
        async with self._sessions() as session:
            session.add(
                AutomationRecordRow(
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
            )
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
            updated = _record(row).model_copy(update=fields)
            row.status = updated.status.value
            row.payload = _record_payload(updated)
            await session.commit()
            return updated
