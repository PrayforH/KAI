"""Automation persistence port and in-memory adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from harness.automations.models import (
    AutomationRunRecord,
    AutomationStatus,
    AutomationTask,
)
from harness.core.errors import ConflictError, NotFoundError


def _rank(tasks: list[AutomationTask]) -> list[AutomationTask]:
    active = [item for item in tasks if item.status is not AutomationStatus.EXPIRED]
    expired = [item for item in tasks if item.status is AutomationStatus.EXPIRED]
    active.sort(key=lambda item: (item.next_run_at or item.created_at, item.task_id))
    expired.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
    return active + expired


class AutomationTaskRepository(Protocol):
    async def add(self, task: AutomationTask) -> None: ...
    async def get(self, tenant_id: str, task_id: str) -> AutomationTask: ...
    async def list_for_user(self, tenant_id: str, user_id: str) -> list[AutomationTask]: ...
    async def replace(self, expected_revision: int, task: AutomationTask) -> None: ...
    async def delete(self, tenant_id: str, task_id: str) -> None: ...
    async def list_due(self, now: datetime, *, limit: int) -> list[AutomationTask]: ...
    async def claim_due(
        self,
        task_id: str,
        *,
        expected_next_run_at: datetime,
        next_run_at: datetime | None,
        status: str,
        last_run_at: datetime,
    ) -> bool: ...


class InMemoryAutomationTaskRepository:
    def __init__(self) -> None:
        self._items: dict[str, AutomationTask] = {}
        self._lock = asyncio.Lock()

    async def add(self, task: AutomationTask) -> None:
        async with self._lock:
            if task.task_id in self._items:
                raise ConflictError(f"Automation Task already exists: {task.task_id}")
            self._items[task.task_id] = task

    async def get(self, tenant_id: str, task_id: str) -> AutomationTask:
        try:
            task = self._items[task_id]
        except KeyError as error:
            raise NotFoundError(f"Automation Task not found: {task_id}") from error
        if task.tenant_id != tenant_id:
            raise NotFoundError(f"Automation Task not found: {task_id}")
        return task

    async def list_for_user(self, tenant_id: str, user_id: str) -> list[AutomationTask]:
        items = [
            item
            for item in self._items.values()
            if item.tenant_id == tenant_id and item.user_id == user_id
        ]
        return _rank(items)

    async def replace(self, expected_revision: int, task: AutomationTask) -> None:
        async with self._lock:
            current = self._items.get(task.task_id)
            if current is None or current.tenant_id != task.tenant_id:
                raise NotFoundError(f"Automation Task not found: {task.task_id}")
            if current.revision != expected_revision:
                raise ConflictError(
                    "Automation Task revision changed: "
                    f"expected={expected_revision} actual={current.revision}"
                )
            if task.revision != expected_revision + 1:
                raise ConflictError("Automation Task replacement must increment revision once")
            self._items[task.task_id] = task

    async def delete(self, tenant_id: str, task_id: str) -> None:
        async with self._lock:
            task = self._items.get(task_id)
            if task is None or task.tenant_id != tenant_id:
                raise NotFoundError(f"Automation Task not found: {task_id}")
            del self._items[task_id]

    async def list_due(self, now: datetime, *, limit: int) -> list[AutomationTask]:
        return sorted(
            (
                item
                for item in self._items.values()
                if item.status is AutomationStatus.ACTIVE
                and item.next_run_at is not None
                and item.next_run_at <= now
            ),
            key=lambda item: (item.next_run_at or now, item.task_id),
        )[:limit]

    async def claim_due(
        self,
        task_id: str,
        *,
        expected_next_run_at: datetime,
        next_run_at: datetime | None,
        status: str,
        last_run_at: datetime,
    ) -> bool:
        async with self._lock:
            current = self._items.get(task_id)
            if current is None or current.next_run_at != expected_next_run_at:
                return False
            self._items[task_id] = current.model_copy(
                update={
                    "next_run_at": next_run_at,
                    "status": AutomationStatus(status),
                    "last_run_at": last_run_at,
                    "updated_at": last_run_at,
                }
            )
            return True


class AutomationRecordRepository(Protocol):
    async def add(self, record: AutomationRunRecord) -> None: ...
    async def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int
    ) -> list[AutomationRunRecord]: ...
    async def update_status(
        self, tenant_id: str, record_id: str, **fields: object
    ) -> AutomationRunRecord | None: ...


class InMemoryAutomationRecordRepository:
    def __init__(self) -> None:
        self._items: dict[str, AutomationRunRecord] = {}
        self._lock = asyncio.Lock()

    async def add(self, record: AutomationRunRecord) -> None:
        async with self._lock:
            self._items[record.record_id] = record

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int
    ) -> list[AutomationRunRecord]:
        items = [
            item
            for item in self._items.values()
            if item.tenant_id == tenant_id and item.user_id == user_id
        ]
        items.sort(key=lambda item: (item.started_at, item.record_id), reverse=True)
        return items[:limit]

    async def update_status(
        self, tenant_id: str, record_id: str, **fields: object
    ) -> AutomationRunRecord | None:
        async with self._lock:
            record = self._items.get(record_id)
            if record is None or record.tenant_id != tenant_id:
                return None
            updated = record.model_copy(update=fields)
            self._items[record_id] = updated
            return updated
