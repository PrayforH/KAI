"""Transactional objective changes and durable, deduplicated execution intents."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.assistant.lifecycle import next_check, transition
from harness.assistant.models import (
    AssistantInboxItem,
    DesktopTurn,
    HandoverStatus,
    ManagedTask,
    Objective,
    ObjectiveAction,
    ObjectiveBrief,
    ObjectiveCheckpoint,
    ObjectiveEvent,
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    ObjectiveStatus,
    Specialist,
    SpecialistHandover,
    SpecialistStatus,
)
from harness.assistant.storage import (
    AssistantInboxRow,
    DesktopTurnRow,
    ManagedTaskRow,
    ObjectiveCheckpointRow,
    ObjectiveEventRow,
    ObjectiveExecutionRow,
    ObjectiveRow,
    SpecialistCredentialRow,
    SpecialistHandoverRow,
    SpecialistRow,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory

_OPEN_EXECUTIONS = (
    ObjectiveExecutionStatus.PENDING.value,
    ObjectiveExecutionStatus.RUNNING.value,
)


def _objective(row: ObjectiveRow) -> Objective:
    return Objective.model_validate(row.payload)


def _store(row: ObjectiveRow, objective: Objective) -> None:
    row.status = objective.status.value
    row.revision = objective.revision
    row.next_check_at = objective.next_check_at
    row.updated_at = objective.updated_at
    row.payload = objective.model_dump(mode="json", by_alias=True)


def _event(
    session: AsyncSession, objective: Objective, kind: str, content: str, now: datetime
) -> None:
    session.add(
        ObjectiveEventRow(
            event_id=f"objective_event_{uuid4().hex}",
            objective_id=objective.objective_id,
            tenant_id=objective.tenant_id,
            user_id=objective.user_id,
            kind=kind,
            content=content,
            created_at=now,
        )
    )


class PostgresAssistantRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions
        self._bound_session: AsyncSession | None = None

    def with_session(self, session: AsyncSession) -> PostgresAssistantRepository:
        """Join a channel transaction so intent, receipt and reply commit together."""
        repository = PostgresAssistantRepository(self._sessions)
        repository._bound_session = session
        return repository

    @asynccontextmanager
    async def _context(self, *, write: bool = False) -> AsyncGenerator[AsyncSession]:
        if self._bound_session is not None:
            yield self._bound_session
        else:
            async with self._sessions.begin() if write else self._sessions() as session:
                yield session

    async def _owned(
        self,
        session: AsyncSession,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        lock: bool = False,
    ) -> ObjectiveRow:
        query = select(ObjectiveRow).where(
            ObjectiveRow.tenant_id == tenant_id,
            ObjectiveRow.user_id == user_id,
            ObjectiveRow.objective_id == objective_id,
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFoundError("Objective not found")
        return row

    async def create(
        self, objective: Objective, *, idempotency_key: str | None = None
    ) -> Objective:
        if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
            raise ValueError("invalid objective idempotency key")
        async with self._context(write=True) as session:
            if idempotency_key is not None:
                existing = await self._find_idempotent(
                    session, objective.tenant_id, objective.user_id, idempotency_key
                )
                if existing is not None:
                    return _objective(existing)
            try:
                async with session.begin_nested():
                    row = ObjectiveRow(
                        objective_id=objective.objective_id,
                        tenant_id=objective.tenant_id,
                        user_id=objective.user_id,
                        idempotency_key=idempotency_key,
                        parent_objective_id=objective.parent_objective_id,
                        created_at=objective.created_at,
                    )
                    _store(row, objective)
                    session.add(row)
                    await session.flush()
                    _event(
                        session, objective, "created", objective.instructions, objective.created_at
                    )
            except IntegrityError:
                # A concurrent caller with the same key won the race; its
                # objective is the one true delegate for this request.
                existing = await self._find_idempotent(
                    session, objective.tenant_id, objective.user_id, idempotency_key or ""
                )
                if existing is None:
                    raise
                return _objective(existing)
        return objective

    @staticmethod
    async def _find_idempotent(
        session: AsyncSession, tenant_id: str, user_id: str, idempotency_key: str
    ) -> ObjectiveRow | None:
        return await session.scalar(
            select(ObjectiveRow).where(
                ObjectiveRow.tenant_id == tenant_id,
                ObjectiveRow.user_id == user_id,
                ObjectiveRow.idempotency_key == idempotency_key,
            )
        )

    async def get(self, tenant_id: str, user_id: str, objective_id: str) -> Objective:
        async with self._context() as session:
            return _objective(await self._owned(session, tenant_id, user_id, objective_id))

    async def list(self, tenant_id: str, user_id: str, *, limit: int = 100) -> list[Objective]:
        async with self._context() as session:
            rows = await session.scalars(
                select(ObjectiveRow)
                .where(ObjectiveRow.tenant_id == tenant_id, ObjectiveRow.user_id == user_id)
                .order_by(ObjectiveRow.updated_at.desc(), ObjectiveRow.objective_id)
                .limit(limit)
            )
            return [_objective(row) for row in rows]

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        expected_revision: int,
        now: datetime,
        brief: ObjectiveBrief | None = None,
        action: ObjectiveAction | None = None,
        note: str | None = None,
    ) -> Objective:
        if sum(value is not None for value in (brief, action, note)) != 1:
            raise ValueError("exactly one objective change is required")
        async with self._context(write=True) as session:
            row = await self._owned(session, tenant_id, user_id, objective_id, lock=True)
            current = _objective(row)
            if current.revision != expected_revision:
                raise ConflictError("Objective changed; reload before applying this change")
            if current.status.terminal:
                raise ConflictError("A finished objective cannot be changed")
            changes: dict[str, object] = {"revision": current.revision + 1, "updated_at": now}
            if brief is not None:
                target = (current.agent_name, current.agent_version, current.project_id)
                if target != (brief.agent_name, brief.agent_version, brief.project_id):
                    if await session.scalar(
                        select(ObjectiveExecutionRow.execution_id)
                        .where(ObjectiveExecutionRow.objective_id == objective_id)
                        .limit(1)
                    ):
                        raise ConflictError(
                            "Create a new objective to change its agent or project after execution"
                        )
                changes.update(brief.model_dump())
                changes["next_check_at"] = (
                    next_check(brief.schedule, after=now, ends_at=brief.ends_at)
                    if current.status is ObjectiveStatus.ACTIVE
                    else None
                )
                kind, content = "updated", brief.instructions
            elif action is not None:
                status = transition(current.status, action)
                changes.update(
                    status=status,
                    next_check_at=(
                        next_check(current.schedule, after=now, ends_at=current.ends_at)
                        if status is ObjectiveStatus.ACTIVE
                        else None
                    ),
                )
                kind, content = action.value, ""
            else:
                kind, content = "note", note or ""
            updated = Objective.model_validate(current.model_dump() | changes)
            _store(row, updated)
            _event(session, updated, kind, content, now)
        return updated

    async def events(
        self, tenant_id: str, user_id: str, objective_id: str, *, limit: int = 100
    ) -> list[ObjectiveEvent]:
        async with self._context() as session:
            await self._owned(session, tenant_id, user_id, objective_id)
            rows = await session.scalars(
                select(ObjectiveEventRow)
                .where(
                    ObjectiveEventRow.objective_id == objective_id,
                    ObjectiveEventRow.tenant_id == tenant_id,
                    ObjectiveEventRow.user_id == user_id,
                )
                .order_by(ObjectiveEventRow.created_at.desc(), ObjectiveEventRow.event_id)
                .limit(limit)
            )
            return [
                ObjectiveEvent(
                    eventId=row.event_id,
                    objectiveId=row.objective_id,
                    kind=row.kind,
                    content=row.content,
                    createdAt=row.created_at,
                )
                for row in rows
            ]

    async def enqueue(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        trigger_key: str,
        now: datetime,
    ) -> ObjectiveExecution:
        if not trigger_key or len(trigger_key) > 256:
            raise ValueError("invalid objective trigger key")
        async with self._context(write=True) as session:
            row = await self._owned(session, tenant_id, user_id, objective_id, lock=True)
            return await self._enqueue(session, row, trigger_key=trigger_key, now=now)

    async def _enqueue(
        self,
        session: AsyncSession,
        row: ObjectiveRow,
        *,
        trigger_key: str,
        now: datetime,
    ) -> ObjectiveExecution:
        existing = await session.scalar(
            select(ObjectiveExecutionRow).where(
                ObjectiveExecutionRow.objective_id == row.objective_id,
                ObjectiveExecutionRow.trigger_key == trigger_key,
            )
        )
        if existing is not None:
            return ObjectiveExecution.model_validate(existing.payload)
        objective = _objective(row)
        if objective.status is not ObjectiveStatus.ACTIVE:
            raise ConflictError("Only active objectives can run")
        if objective.mode.value == "follow_only":
            raise ConflictError("A follow-only objective does not start executions")
        if objective.ends_at is not None and objective.ends_at <= now:
            raise ConflictError("The objective's execution period has ended")
        if await session.scalar(
            select(ObjectiveExecutionRow.execution_id)
            .where(
                ObjectiveExecutionRow.objective_id == objective.objective_id,
                ObjectiveExecutionRow.status.in_(_OPEN_EXECUTIONS),
            )
            .limit(1)
        ):
            raise ConflictError("This objective already has an active execution")
        budget = objective.budget
        if budget is not None:
            # 派发前的预算闸门：在同一个入队事务内计数，达到上限即拒绝。
            if budget.max_total_runs is not None:
                total = await session.scalar(
                    select(func.count())
                    .select_from(ObjectiveExecutionRow)
                    .where(ObjectiveExecutionRow.objective_id == objective.objective_id)
                )
                if (total or 0) >= budget.max_total_runs:
                    raise ConflictError("预算已用尽：达到总运行次数上限，不再派发新执行")
            if budget.max_runs_per_day is not None:
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                today = await session.scalar(
                    select(func.count())
                    .select_from(ObjectiveExecutionRow)
                    .where(
                        ObjectiveExecutionRow.objective_id == objective.objective_id,
                        ObjectiveExecutionRow.created_at >= day_start,
                    )
                )
                if (today or 0) >= budget.max_runs_per_day:
                    raise ConflictError("预算已用尽：达到当日运行次数上限，不再派发新执行")
            if budget.max_tokens is not None or budget.max_cost_usd is not None:
                used_tokens = await session.scalar(
                    select(func.coalesce(func.sum(ObjectiveCheckpointRow.tokens), 0)).where(
                        ObjectiveCheckpointRow.objective_id == objective.objective_id
                    )
                )
                used_cost = await session.scalar(
                    select(func.coalesce(func.sum(ObjectiveCheckpointRow.cost_usd), 0)).where(
                        ObjectiveCheckpointRow.objective_id == objective.objective_id
                    )
                )
                unknown = await session.scalar(
                    select(func.count())
                    .select_from(ObjectiveCheckpointRow)
                    .where(
                        ObjectiveCheckpointRow.objective_id == objective.objective_id,
                        ObjectiveCheckpointRow.unknown_usage.is_(True),
                    )
                )
                if budget.max_tokens is not None and int(used_tokens or 0) >= budget.max_tokens:
                    raise ConflictError("预算已用尽：达到 token 上限，不再派发新执行")
                if (
                    budget.max_cost_usd is not None
                    and float(used_cost or 0) >= budget.max_cost_usd
                ):
                    raise ConflictError("预算已用尽：达到费用上限，不再派发新执行")
                if unknown and budget.max_tokens is not None:
                    # 有历史执行没有可靠计量：不能声称仍在额度内。
                    raise ConflictError(
                        "token 预算无法核实：存在未计量执行，请先核对用量再派发"
                    )
        notes = list(
            await session.scalars(
                select(ObjectiveEventRow.content)
                .where(
                    ObjectiveEventRow.objective_id == objective.objective_id,
                    ObjectiveEventRow.kind == "note",
                )
                .order_by(ObjectiveEventRow.created_at.desc(), ObjectiveEventRow.event_id)
                .limit(20)
            )
        )
        digest = hashlib.sha256(f"{objective.objective_id}\0{trigger_key}".encode()).hexdigest()
        brief = ObjectiveBrief.model_validate(
            objective.model_dump(include=set(ObjectiveBrief.model_fields))
        )
        execution = ObjectiveExecution(
            executionId=f"objective_run_{digest[:32]}",
            objectiveId=objective.objective_id,
            tenantId=objective.tenant_id,
            userId=objective.user_id,
            triggerKey=trigger_key,
            objectiveRevision=objective.revision,
            brief=brief,
            context="\n\n".join(reversed(notes))[-32_000:],
            threadId=objective.managed_thread_id or f"assistant_{objective.objective_id}",
            createdAt=now,
        )
        session.add(
            ObjectiveExecutionRow(
                execution_id=execution.execution_id,
                objective_id=execution.objective_id,
                tenant_id=execution.tenant_id,
                user_id=execution.user_id,
                trigger_key=trigger_key,
                status=execution.status.value,
                created_at=now,
                payload=execution.model_dump(mode="json", by_alias=True),
            )
        )
        _event(session, objective, "execution_requested", execution.execution_id, now)
        return execution

    async def executions(
        self, tenant_id: str, user_id: str, objective_id: str
    ) -> list[ObjectiveExecution]:
        async with self._context() as session:
            await self._owned(session, tenant_id, user_id, objective_id)
            rows = await session.scalars(
                select(ObjectiveExecutionRow)
                .where(
                    ObjectiveExecutionRow.objective_id == objective_id,
                    ObjectiveExecutionRow.tenant_id == tenant_id,
                    ObjectiveExecutionRow.user_id == user_id,
                )
                .order_by(ObjectiveExecutionRow.created_at.desc())
                .limit(100)
            )
            return [ObjectiveExecution.model_validate(row.payload) for row in rows]

    async def schedule_due(self, now: datetime, *, limit: int = 20) -> None:
        async with self._context(write=True) as session:
            rows = await session.scalars(
                select(ObjectiveRow)
                .where(
                    ObjectiveRow.status == ObjectiveStatus.ACTIVE.value,
                    ObjectiveRow.next_check_at <= now,
                )
                .order_by(ObjectiveRow.next_check_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            for row in rows:
                current = _objective(row)
                try:
                    await self._enqueue(
                        session,
                        row,
                        trigger_key=f"schedule:{current.next_check_at}",
                        now=now,
                    )
                except ConflictError:
                    # A busy objective skips this interval, without a catch-up burst.
                    pass
                updated = current.model_copy(
                    update={
                        "next_check_at": next_check(
                            current.schedule, after=now, ends_at=current.ends_at
                        )
                    }
                )
                _store(row, updated)

    async def claim(self, now: datetime) -> tuple[ObjectiveExecution, str] | None:
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(ObjectiveExecutionRow)
                .where(
                    ObjectiveExecutionRow.status.in_(_OPEN_EXECUTIONS),
                    or_(
                        ObjectiveExecutionRow.lease_until.is_(None),
                        ObjectiveExecutionRow.lease_until <= now,
                    ),
                )
                .order_by(
                    ObjectiveExecutionRow.lease_until.asc().nullsfirst(),
                    ObjectiveExecutionRow.created_at,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            token = uuid4().hex
            row.lease_token, row.lease_until = token, now + timedelta(seconds=60)
            return ObjectiveExecution.model_validate(row.payload), token

    async def save_execution(
        self,
        execution: ObjectiveExecution,
        token: str,
        now: datetime,
        *,
        usage_tokens: int | None = None,
        usage_cost: float | None = None,
    ) -> bool:
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(ObjectiveExecutionRow)
                .where(
                    ObjectiveExecutionRow.execution_id == execution.execution_id,
                    ObjectiveExecutionRow.lease_token == token,
                    ObjectiveExecutionRow.status.in_(_OPEN_EXECUTIONS),
                    ObjectiveExecutionRow.lease_until > now,
                )
                .with_for_update()
            )
            if row is None:
                return False
            row.status = execution.status.value
            row.payload = execution.model_dump(mode="json", by_alias=True)
            # Poll running executions fairly; a crashed owner is retried after the lease.
            row.lease_until = now + timedelta(seconds=5)
            if execution.finished_at is not None:
                objective = _objective(
                    await self._owned(
                        session, execution.tenant_id, execution.user_id, execution.objective_id
                    )
                )
                _event(session, objective, "execution_finished", execution.execution_id, now)
                summary = execution.summary or "本次执行已结束，请查看任务记录。"
                session.add(
                    ObjectiveCheckpointRow(
                        checkpoint_id=f"checkpoint_{execution.execution_id}",
                        objective_id=execution.objective_id,
                        execution_id=execution.execution_id,
                        tenant_id=execution.tenant_id,
                        user_id=execution.user_id,
                        run_id=execution.run_id,
                        revision=execution.objective_revision,
                        status=execution.status.value,
                        summary=summary,
                        tokens=usage_tokens,
                        cost_usd=usage_cost,
                        unknown_usage=usage_tokens is None and usage_cost is None,
                        created_at=now,
                    )
                )
                item = AssistantInboxItem(
                    itemId=f"notice_{execution.execution_id}",
                    objectiveId=execution.objective_id,
                    executionId=execution.execution_id,
                    kind=execution.status.value,
                    title=objective.title,
                    content=summary,
                    threadId=execution.thread_id,
                    createdAt=now,
                )
                session.add(
                    AssistantInboxRow(
                        item_id=item.item_id,
                        objective_id=item.objective_id,
                        tenant_id=execution.tenant_id,
                        user_id=execution.user_id,
                        dedup_key=execution.execution_id,
                        created_at=now,
                        payload=item.model_dump(mode="json", by_alias=True),
                    )
                )
            return True

    async def bind_managed(self, task: ManagedTask) -> tuple[ManagedTask, bool]:
        """Bind an objective to an original thread; the execute slot is unique.

        Returns the durable binding and whether this call created it, so a
        retried delegation reports the existing coordinator instead of a
        duplicate takeover. A declared dependency must not close a cycle:
        the chain is walked with a depth cap before the row is written.
        """
        await self._ensure_acyclic(task)
        async with self._context(write=True) as session:
            existing = await session.scalar(
                select(ManagedTaskRow).where(
                    ManagedTaskRow.tenant_id == task.tenant_id,
                    ManagedTaskRow.user_id == task.user_id,
                    ManagedTaskRow.thread_id == task.thread_id,
                    ManagedTaskRow.role == task.role.value,
                )
            )
            if existing is not None:
                return ManagedTask.model_validate(existing.payload), False
            try:
                async with session.begin_nested():
                    session.add(
                        ManagedTaskRow(
                            managed_id=task.managed_id,
                            objective_id=task.objective_id,
                            tenant_id=task.tenant_id,
                            user_id=task.user_id,
                            thread_id=task.thread_id,
                            project_id=task.project_id,
                            depends_on_objective_id=task.depends_on_objective_id,
                            role=task.role.value,
                            source=task.source.value,
                            created_at=task.created_at,
                            payload=task.model_dump(mode="json", by_alias=True),
                        )
                    )
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(ManagedTaskRow).where(
                        ManagedTaskRow.tenant_id == task.tenant_id,
                        ManagedTaskRow.user_id == task.user_id,
                        ManagedTaskRow.thread_id == task.thread_id,
                        ManagedTaskRow.role == task.role.value,
                    )
                )
                if existing is None:
                    raise
                return ManagedTask.model_validate(existing.payload), False
        return task, True

    async def _ensure_acyclic(self, task: ManagedTask, *, max_depth: int = 8) -> None:
        """依赖禁止成环：沿 depends_on 链回走，回到自身或超深即拒绝。"""
        seen = {task.objective_id}
        current = task.depends_on_objective_id
        depth = 0
        while current is not None:
            if current in seen or current == task.objective_id:
                raise ConflictError("任务依赖成环；请调整依赖关系")
            seen.add(current)
            depth += 1
            if depth > max_depth:
                raise ConflictError("依赖链过深；请扁平化任务结构")
            bindings = await self.managed_bindings(
                task.tenant_id, task.user_id, objective_id=current
            )
            current = next(
                (
                    item.depends_on_objective_id
                    for item in bindings
                    if item.depends_on_objective_id
                ),
                None,
            )

    async def count_children(self, tenant_id: str, user_id: str, objective_id: str) -> int:
        async with self._context() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ObjectiveRow)
                .where(
                    ObjectiveRow.tenant_id == tenant_id,
                    ObjectiveRow.user_id == user_id,
                    ObjectiveRow.parent_objective_id == objective_id,
                )
            )
            return int(total or 0)

    async def managed_bindings(
        self,
        tenant_id: str,
        user_id: str,
        *,
        objective_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[ManagedTask]:
        clauses = [ManagedTaskRow.tenant_id == tenant_id, ManagedTaskRow.user_id == user_id]
        if objective_id is not None:
            clauses.append(ManagedTaskRow.objective_id == objective_id)
        if thread_id is not None:
            clauses.append(ManagedTaskRow.thread_id == thread_id)
        async with self._context() as session:
            rows = await session.scalars(
                select(ManagedTaskRow)
                .where(*clauses)
                .order_by(ManagedTaskRow.created_at.desc())
                .limit(100)
            )
            return [ManagedTask.model_validate(row.payload) for row in rows]

    async def save_checkpoint(self, checkpoint: ObjectiveCheckpoint) -> None:
        async with self._context(write=True) as session:
            objective = await self._owned(
                session, checkpoint.tenant_id, checkpoint.user_id, checkpoint.objective_id
            )
            session.add(
                ObjectiveCheckpointRow(
                    checkpoint_id=checkpoint.checkpoint_id,
                    objective_id=checkpoint.objective_id,
                    execution_id=checkpoint.execution_id,
                    tenant_id=checkpoint.tenant_id,
                    user_id=checkpoint.user_id,
                    run_id=checkpoint.run_id,
                    revision=checkpoint.revision,
                    status=checkpoint.status.value,
                    summary=checkpoint.summary,
                    created_at=checkpoint.created_at,
                )
            )
            _event(
                session,
                _objective(objective),
                "checkpoint",
                checkpoint.summary or checkpoint.status.value,
                checkpoint.created_at,
            )

    async def checkpoints(
        self, tenant_id: str, user_id: str, objective_id: str, *, limit: int = 10
    ) -> list[ObjectiveCheckpoint]:
        async with self._context() as session:
            await self._owned(session, tenant_id, user_id, objective_id)
            rows = await session.scalars(
                select(ObjectiveCheckpointRow)
                .where(
                    ObjectiveCheckpointRow.objective_id == objective_id,
                    ObjectiveCheckpointRow.tenant_id == tenant_id,
                    ObjectiveCheckpointRow.user_id == user_id,
                )
                .order_by(ObjectiveCheckpointRow.created_at.desc())
                .limit(limit)
            )
            return [
                ObjectiveCheckpoint(
                    checkpointId=row.checkpoint_id,
                    objectiveId=row.objective_id,
                    executionId=row.execution_id,
                    tenantId=row.tenant_id,
                    userId=row.user_id,
                    runId=row.run_id,
                    revision=row.revision,
                    status=ObjectiveExecutionStatus(row.status),
                    summary=row.summary,
                    tokens=row.tokens,
                    costUsd=row.cost_usd,
                    unknownUsage=row.unknown_usage,
                    createdAt=row.created_at,
                )
                for row in rows
            ]

    async def inbox(self, tenant_id: str, user_id: str) -> list[AssistantInboxItem]:
        async with self._context() as session:
            rows = await session.scalars(
                select(AssistantInboxRow)
                .where(
                    AssistantInboxRow.tenant_id == tenant_id,
                    AssistantInboxRow.user_id == user_id,
                )
                .order_by(AssistantInboxRow.created_at.desc())
                .limit(100)
            )
            return [
                AssistantInboxItem.model_validate(row.payload).model_copy(
                    update={"read_at": row.read_at}
                )
                for row in rows
            ]

    async def read_notice(self, tenant_id: str, user_id: str, item_id: str, now: datetime) -> None:
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(AssistantInboxRow)
                .where(
                    AssistantInboxRow.tenant_id == tenant_id,
                    AssistantInboxRow.user_id == user_id,
                    AssistantInboxRow.item_id == item_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("Notice not found")
            row.read_at = row.read_at or now

    async def create_specialist(self, specialist: Specialist) -> Specialist:
        """Create a specialist; the slug is the dedupe key inside one user."""
        async with self._context(write=True) as session:
            existing = await session.scalar(
                select(SpecialistRow).where(
                    SpecialistRow.tenant_id == specialist.tenant_id,
                    SpecialistRow.user_id == specialist.user_id,
                    SpecialistRow.slug == specialist.slug,
                )
            )
            if existing is not None:
                return Specialist.model_validate(existing.payload)
            try:
                async with session.begin_nested():
                    session.add(
                        SpecialistRow(
                            specialist_id=specialist.specialist_id,
                            tenant_id=specialist.tenant_id,
                            user_id=specialist.user_id,
                            slug=specialist.slug,
                            status=specialist.status.value,
                            revision=specialist.revision,
                            created_at=specialist.created_at,
                            updated_at=specialist.updated_at,
                            payload=specialist.model_dump(mode="json", by_alias=True),
                        )
                    )
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(SpecialistRow).where(
                        SpecialistRow.tenant_id == specialist.tenant_id,
                        SpecialistRow.user_id == specialist.user_id,
                        SpecialistRow.slug == specialist.slug,
                    )
                )
                if existing is None:
                    raise
                return Specialist.model_validate(existing.payload)
        return specialist

    async def get_specialist(
        self, tenant_id: str, user_id: str, specialist_id: str
    ) -> Specialist:
        async with self._context() as session:
            row = await session.scalar(
                select(SpecialistRow).where(
                    SpecialistRow.tenant_id == tenant_id,
                    SpecialistRow.user_id == user_id,
                    SpecialistRow.specialist_id == specialist_id,
                )
            )
            if row is None:
                raise NotFoundError("Specialist not found")
            return Specialist.model_validate(row.payload)

    async def list_specialists(self, tenant_id: str, user_id: str) -> list[Specialist]:
        async with self._context() as session:
            rows = await session.scalars(
                select(SpecialistRow)
                .where(SpecialistRow.tenant_id == tenant_id, SpecialistRow.user_id == user_id)
                .order_by(SpecialistRow.updated_at.desc())
                .limit(100)
            )
            return [Specialist.model_validate(row.payload) for row in rows]

    async def update_specialist(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        expected_revision: int,
        config: dict[str, object] | None = None,
        status: SpecialistStatus | None = None,
        now: datetime,
    ) -> Specialist:
        if config is None and status is None:
            raise ValueError("exactly one specialist change is required")
        if config is not None and status is not None:
            raise ValueError("exactly one specialist change is required")
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(SpecialistRow)
                .where(
                    SpecialistRow.tenant_id == tenant_id,
                    SpecialistRow.user_id == user_id,
                    SpecialistRow.specialist_id == specialist_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("Specialist not found")
            current = Specialist.model_validate(row.payload)
            if current.revision != expected_revision:
                raise ConflictError("Specialist changed; reload before applying this change")
            changes: dict[str, object] = {"revision": current.revision + 1, "updated_at": now}
            if config is not None:
                merged = dict(current.config)
                merged.update(config)
                changes["config"] = merged
            if status is not None:
                changes["status"] = status.value
            updated = Specialist.model_validate(current.model_dump() | changes)
            row.status = updated.status.value
            row.revision = updated.revision
            row.updated_at = now
            row.payload = updated.model_dump(mode="json", by_alias=True)
        return updated

    async def record_handover(self, handover: SpecialistHandover) -> SpecialistHandover:
        """Idempotent on the source message id: replays never duplicate receipts."""
        async with self._context(write=True) as session:
            existing = await session.scalar(
                select(SpecialistHandoverRow).where(
                    SpecialistHandoverRow.tenant_id == handover.tenant_id,
                    SpecialistHandoverRow.user_id == handover.user_id,
                    SpecialistHandoverRow.message_id == handover.message_id,
                )
            )
            if existing is not None:
                return SpecialistHandover.model_validate(existing.payload)
            session.add(
                SpecialistHandoverRow(
                    handover_id=handover.handover_id,
                    specialist_id=handover.specialist_id,
                    objective_id=handover.objective_id,
                    tenant_id=handover.tenant_id,
                    user_id=handover.user_id,
                    message_id=handover.message_id,
                    status=handover.status.value,
                    created_at=handover.created_at,
                    updated_at=handover.updated_at,
                    payload=handover.model_dump(mode="json", by_alias=True),
                )
            )
        return handover

    async def update_handover(
        self,
        tenant_id: str,
        user_id: str,
        handover_id: str,
        *,
        status: HandoverStatus,
        now: datetime,
    ) -> SpecialistHandover:
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(SpecialistHandoverRow)
                .where(
                    SpecialistHandoverRow.tenant_id == tenant_id,
                    SpecialistHandoverRow.user_id == user_id,
                    SpecialistHandoverRow.handover_id == handover_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("Handover not found")
            current = SpecialistHandover.model_validate(row.payload)
            updated = current.model_copy(update={"status": status, "updatedAt": now})
            row.status = status.value
            row.updated_at = now
            row.payload = updated.model_dump(mode="json", by_alias=True)
            return updated

    async def handovers(
        self, tenant_id: str, user_id: str, *, specialist_id: str | None = None
    ) -> list[SpecialistHandover]:
        clauses = [
            SpecialistHandoverRow.tenant_id == tenant_id,
            SpecialistHandoverRow.user_id == user_id,
        ]
        if specialist_id is not None:
            clauses.append(SpecialistHandoverRow.specialist_id == specialist_id)
        async with self._context() as session:
            rows = await session.scalars(
                select(SpecialistHandoverRow)
                .where(*clauses)
                .order_by(SpecialistHandoverRow.created_at.desc())
                .limit(50)
            )
            return [SpecialistHandover.model_validate(row.payload) for row in rows]

    async def save_credential_blob(
        self,
        tenant_id: str,
        user_id: str,
        reference: str,
        ciphertext: str,
        *,
        now: datetime,
    ) -> None:
        """Encrypted value storage; only the reference is ever handed back."""
        async with self._context(write=True) as session:
            session.add(
                SpecialistCredentialRow(
                    credential_id=f"credential_{uuid4().hex}",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    reference=reference,
                    ciphertext=ciphertext,
                    created_at=now,
                )
            )

    async def acquire_desktop_turn(
        self,
        *,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        execution_id: str,
        now: datetime,
        ttl_seconds: int = 900,
    ) -> DesktopTurn | None:
        """获取共享桌面的一次执行权；已有他人持有则返回 None（排队等待）。

        过期的持有先释放，避免崩溃残留永久堵住桌面。同一用户同一时刻只有
        一个执行者，这个约束由部分唯一索引在数据库层兜底。
        """
        async with self._context(write=True) as session:
            active = await session.scalar(
                select(DesktopTurnRow)
                .where(
                    DesktopTurnRow.tenant_id == tenant_id,
                    DesktopTurnRow.user_id == user_id,
                    DesktopTurnRow.released_at.is_(None),
                )
                .with_for_update()
            )
            if active is not None:
                if active.execution_id == execution_id:
                    # 同一执行重复领取（重建时）直接复用，不重复排队。
                    return DesktopTurn(
                        desktopTurnId=active.desktop_turn_id,
                        objectiveId=active.objective_id,
                        tenantId=active.tenant_id,
                        userId=active.user_id,
                        executionId=active.execution_id,
                        acquiredAt=active.acquired_at,
                        expiresAt=active.expires_at,
                        releasedAt=active.released_at,
                    )
                if active.expires_at > now:
                    return None
                active.released_at = now
            turn = DesktopTurn(
                desktopTurnId=f"desktop_turn_{uuid4().hex}",
                objectiveId=objective_id,
                tenantId=tenant_id,
                userId=user_id,
                executionId=execution_id,
                acquiredAt=now,
                expiresAt=now + timedelta(seconds=ttl_seconds),
            )
            session.add(
                DesktopTurnRow(
                    desktop_turn_id=turn.desktop_turn_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    objective_id=objective_id,
                    execution_id=execution_id,
                    acquired_at=now,
                    expires_at=turn.expires_at,
                )
            )
        return turn

    async def release_desktop_turn(
        self, *, tenant_id: str, user_id: str, execution_id: str, now: datetime
    ) -> None:
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(DesktopTurnRow).where(
                    DesktopTurnRow.tenant_id == tenant_id,
                    DesktopTurnRow.user_id == user_id,
                    DesktopTurnRow.execution_id == execution_id,
                    DesktopTurnRow.released_at.is_(None),
                )
            )
            if row is not None:
                row.released_at = now

    async def active_desktop_turn(
        self, tenant_id: str, user_id: str
    ) -> DesktopTurn | None:
        async with self._context() as session:
            row = await session.scalar(
                select(DesktopTurnRow).where(
                    DesktopTurnRow.tenant_id == tenant_id,
                    DesktopTurnRow.user_id == user_id,
                    DesktopTurnRow.released_at.is_(None),
                )
            )
            if row is None:
                return None
            return DesktopTurn(
                desktopTurnId=row.desktop_turn_id,
                objectiveId=row.objective_id,
                tenantId=row.tenant_id,
                userId=row.user_id,
                executionId=row.execution_id,
                acquiredAt=row.acquired_at,
                expiresAt=row.expires_at,
                releasedAt=row.released_at,
            )
