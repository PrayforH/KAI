"""Use cases for scheduled automation tasks and their run records."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from harness.application.runs import RunService
from harness.application.sessions import SessionService
from harness.automations.cronexpr import next_fire_after
from harness.automations.models import (
    AutomationRecordStatus,
    AutomationRecordTrigger,
    AutomationRunRecord,
    AutomationSchedule,
    AutomationScheduleType,
    AutomationStatus,
    AutomationTask,
    AutomationValidity,
    AutomationValidityType,
    CreateAutomationTaskRequest,
    UpdateAutomationTaskRequest,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import RunStatus
from harness.core.ports import AgentRegistry


def _default_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _initial_next_run_at(
    schedule: AutomationSchedule,
    validity: AutomationValidity,
    *,
    now: datetime,
) -> datetime | None:
    if schedule.type is AutomationScheduleType.ONCE:
        return schedule.at
    candidate = next_fire_after(schedule.cron or "", now, timezone=schedule.timezone)
    if (
        validity.type is AutomationValidityType.UNTIL
        and validity.until is not None
        and candidate > validity.until
    ):
        return None
    return candidate


class AutomationService:
    def __init__(
        self,
        tasks,  # AutomationTaskRepository
        records,  # AutomationRecordRepository
        *,
        sessions: SessionService,
        runs: RunService,
        agent_name: str,
        registry: AgentRegistry | None = None,
        agent_version: str = "",
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
    ) -> None:
        self._tasks = tasks
        self._records = records
        self._sessions = sessions
        self._runs = runs
        self._agent_name = agent_name
        self._registry = registry
        self._agent_version = agent_version
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = id_generator or _default_id

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: CreateAutomationTaskRequest,
    ) -> AutomationTask:
        now = self._clock()
        if (
            request.validity.type is AutomationValidityType.UNTIL
            and request.validity.until is not None
            and request.validity.until <= now
        ):
            raise ConflictError("automation validity must end in the future")
        task = AutomationTask(
            tenantId=tenant_id,
            taskId=self._ids("automation"),
            userId=user_id,
            name=request.name.strip(),
            prompt=request.prompt,
            model=request.model or "auto",
            workspaceId=request.workspace_id,
            permission=request.permission,
            schedule=request.schedule,
            validity=request.validity,
            status=AutomationStatus.ACTIVE,
            revision=1,
            sourceTemplateId=request.source_template_id,
            createdAt=now,
            updatedAt=now,
            nextRunAt=_initial_next_run_at(request.schedule, request.validity, now=now),
        )
        if task.next_run_at is None:
            task = task.model_copy(update={"status": AutomationStatus.EXPIRED})
        await self._tasks.add(task)
        return task

    async def list(self, tenant_id: str, user_id: str) -> list[AutomationTask]:
        return await self._tasks.list_for_user(tenant_id, user_id)

    async def get(self, tenant_id: str, user_id: str, task_id: str) -> AutomationTask:
        task = await self._tasks.get(tenant_id, task_id)
        if task.user_id != user_id:
            raise NotFoundError(f"Automation Task not found: {task_id}")
        return task

    async def update(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
        request: UpdateAutomationTaskRequest,
    ) -> AutomationTask:
        current = await self.get(tenant_id, user_id, task_id)
        now = self._clock()
        next_run_at = _initial_next_run_at(request.schedule, request.validity, now=now)
        status = current.status
        if status is AutomationStatus.EXPIRED and next_run_at is not None:
            status = AutomationStatus.ACTIVE
        if next_run_at is None:
            status = AutomationStatus.EXPIRED
        updated = current.model_copy(
            update={
                "name": request.name.strip(),
                "prompt": request.prompt,
                "model": request.model or "auto",
                "workspace_id": request.workspace_id,
                "permission": request.permission,
                "schedule": request.schedule,
                "validity": request.validity,
                "next_run_at": next_run_at,
                "status": status,
                "revision": current.revision + 1,
                "updated_at": now,
            }
        )
        await self._tasks.replace(request.expected_revision, updated)
        return updated

    async def set_enabled(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
        expected_revision: int,
        enabled: bool,
    ) -> AutomationTask:
        current = await self.get(tenant_id, user_id, task_id)
        now = self._clock()
        if enabled:
            next_run_at = _initial_next_run_at(current.schedule, current.validity, now=now)
            status = (
                AutomationStatus.ACTIVE if next_run_at is not None else AutomationStatus.EXPIRED
            )
        else:
            next_run_at = current.next_run_at
            status = AutomationStatus.PAUSED
        updated = current.model_copy(
            update={
                "status": status,
                "next_run_at": next_run_at,
                "revision": current.revision + 1,
                "updated_at": now,
            }
        )
        await self._tasks.replace(expected_revision, updated)
        return updated

    async def delete(self, *, tenant_id: str, user_id: str, task_id: str) -> None:
        await self.get(tenant_id, user_id, task_id)
        await self._tasks.delete(tenant_id, task_id)

    async def preview_next_runs(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
        count: int = 5,
    ) -> list[datetime]:
        task = await self.get(tenant_id, user_id, task_id)
        if task.schedule.type is AutomationScheduleType.ONCE:
            return [task.schedule.at] if task.schedule.at else []
        candidate = self._clock()
        results: list[datetime] = []
        for _ in range(count):
            candidate = next_fire_after(
                task.schedule.cron or "", candidate, timezone=task.schedule.timezone
            )
            results.append(candidate)
        return results

    async def run_now(
        self,
        *,
        tenant_id: str,
        user_id: str,
        task_id: str,
    ) -> AutomationRunRecord:
        task = await self.get(tenant_id, user_id, task_id)
        return await self._execute(
            task,
            trigger=AutomationRecordTrigger.MANUAL,
            scheduled_at=None,
        )

    async def dispatch_due(self, *, limit: int = 50) -> int:
        """Fire every due automation task exactly once.

        The claim step advances ``next_run_at`` before the Agent Run is created,
        so a fleet of workers cannot double-fire the same slot; if invocation
        fails the slot is handed back for a later retry.
        """

        now = self._clock()
        dispatched = 0
        for task in await self._tasks.list_due(now, limit=limit):
            scheduled_at = task.next_run_at
            if scheduled_at is None:
                continue
            next_run_at, status = self._advance(task, from_time=scheduled_at)
            claimed = await self._tasks.claim_due(
                task.task_id,
                expected_next_run_at=scheduled_at,
                next_run_at=next_run_at,
                status=status.value,
                last_run_at=now,
            )
            if not claimed:
                continue
            try:
                await self._execute(
                    task,
                    trigger=AutomationRecordTrigger.SCHEDULED,
                    scheduled_at=scheduled_at,
                )
            except Exception:
                # Make the deterministic slot eligible for retry when dispatch
                # fails before a worker can own the resulting Run.
                await self._tasks.claim_due(
                    task.task_id,
                    expected_next_run_at=next_run_at,
                    next_run_at=scheduled_at,
                    status=AutomationStatus.ACTIVE.value,
                    last_run_at=task.last_run_at or now,
                )
                raise
            dispatched += 1
        return dispatched

    async def _resolve_agent_version(self, task: AutomationTask) -> str:
        """Pin the same Agent version the owner's console sessions would use.

        An explicit configured version wins; otherwise the owner's latest
        published version of the automation agent is resolved from the
        registry, mirroring how new chat sessions are pinned on the web.
        """

        if self._agent_version:
            return self._agent_version
        if self._registry is None:
            raise ConflictError("automation agent version resolution is unavailable")
        versions = [
            item
            for item in await self._registry.list_catalog_for_user(
                task.tenant_id, task.user_id
            )
            if item.name == self._agent_name and item.status.value == "published"
        ]
        if not versions:
            raise ConflictError(
                f"automation agent has no published version: {self._agent_name}"
            )
        return max(versions, key=lambda item: (item.created_at, item.version)).version

    def _advance(
        self,
        task: AutomationTask,
        *,
        from_time: datetime,
    ) -> tuple[datetime | None, AutomationStatus]:
        """Compute the next run slot and status after firing ``from_time``."""

        if task.schedule.type is AutomationScheduleType.ONCE:
            return None, AutomationStatus.EXPIRED
        candidate = next_fire_after(
            task.schedule.cron or "", from_time, timezone=task.schedule.timezone
        )
        if (
            task.validity.type is AutomationValidityType.UNTIL
            and task.validity.until is not None
            and candidate > task.validity.until
        ):
            return None, AutomationStatus.EXPIRED
        return candidate, AutomationStatus.ACTIVE

    async def records(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 100,
    ) -> list[AutomationRunRecord]:
        stored = await self._records.list_for_user(tenant_id, user_id, limit=limit)
        enriched: list[AutomationRunRecord] = []
        for record in stored:
            enriched.append(await self._with_live_status(tenant_id, record))
        return enriched

    async def _with_live_status(
        self, tenant_id: str, record: AutomationRunRecord
    ) -> AutomationRunRecord:
        if record.status is not AutomationRecordStatus.RUNNING:
            return record
        try:
            run = await self._runs.get(tenant_id, record.run_id)
        except (NotFoundError, ConflictError):
            return record
        fields: dict[str, object] = {}
        if run.status is RunStatus.SUCCEEDED:
            fields["status"] = AutomationRecordStatus.SUCCESS.value
        elif run.status is RunStatus.CANCELLED:
            fields["status"] = AutomationRecordStatus.CANCELLED.value
        elif run.status.is_terminal:
            fields["status"] = AutomationRecordStatus.FAILED.value
            fields["error"] = run.error_code or run.status.value
        if fields:
            fields["finished_at"] = run.updated_at
            fields["duration_ms"] = max(
                0, int((run.updated_at - run.created_at).total_seconds() * 1000)
            )
            updated = await self._records.update_status(
                tenant_id, record.record_id, **fields
            )
            if updated is not None:
                # model_copy bypasses validation; re-coerce raw status strings.
                return AutomationRunRecord.model_validate(
                    updated.model_dump(mode="json", by_alias=True)
                )
        return record

    async def _execute(
        self,
        task: AutomationTask,
        *,
        trigger: AutomationRecordTrigger,
        scheduled_at: datetime | None,
    ) -> AutomationRunRecord:
        now = self._clock()
        record = AutomationRunRecord(
            tenantId=task.tenant_id,
            recordId=self._ids("automationrun"),
            taskId=task.task_id,
            taskName=task.name,
            userId=task.user_id,
            trigger=trigger,
            status=AutomationRecordStatus.RUNNING,
            sessionId="",
            runId="",
            scheduledAt=scheduled_at,
            startedAt=now,
        )
        session_id = f"automation_session_{record.record_id}"
        workload_id = f"automation:{task.task_id}"
        prompt = task.prompt
        if task.model and task.model != "auto":
            prompt = f"[model:{task.model}]\n{task.prompt}"
        agent_version = await self._resolve_agent_version(task)
        session = await self._sessions.create(
            task.tenant_id,
            workload_id,
            self._agent_name,
            agent_version,
            session_id=session_id,
            api_key_id=task.task_id,
            agent_owner_user_id=task.user_id,
        )
        run = await self._runs.create(
            task.tenant_id,
            session.session_id,
            f"automation:{record.record_id}",
            input={
                "prompt": prompt,
                "automation_task_id": task.task_id,
                "automation_record_id": record.record_id,
                "automation_trigger": trigger.value,
                "automation_permission": task.permission.value,
                **(
                    {"automation_workspace_id": task.workspace_id}
                    if task.workspace_id
                    else {}
                ),
            },
        )
        record = record.model_copy(
            update={"session_id": session.session_id, "run_id": run.run_id}
        )
        await self._records.add(record)
        return record
