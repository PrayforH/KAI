"""Transactional objective changes and durable, deduplicated execution intents."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import cast as type_cast
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import DateTime, Select, and_, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.assistant.lifecycle import next_check, transition
from harness.assistant.models import (
    AssistantInboxItem,
    AssistantInboxMetrics,
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
from harness.assistant.permissions import require_local_tools
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
from harness.core.events import RunEvent
from harness.storage.database import SessionFactory
from harness.storage.models import AguiThreadBindingRow, EventRow, RunRow, SessionRow

MANAGED_PROGRESS_EVENTS = (
    "run.succeeded",
    "run.failed",
    "run.rejected",
    "run.timed_out",
    "run.cancelled",
    "artifact.ready",
)

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
        if objective.specialist_id:
            specialist_row = await session.scalar(
                select(SpecialistRow).where(
                    SpecialistRow.tenant_id == objective.tenant_id,
                    SpecialistRow.user_id == objective.user_id,
                    SpecialistRow.specialist_id == objective.specialist_id,
                )
            )
            if specialist_row is None:
                raise ConflictError("The objective's specialist no longer exists")
            specialist = Specialist.model_validate(specialist_row.payload)
            require_local_tools(specialist.config)
            if specialist.status not in {SpecialistStatus.READY, SpecialistStatus.ACTIVE}:
                raise ConflictError("子助理尚未就绪或已暂停，不能派发新执行")
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
                if budget.max_cost_usd is not None and float(used_cost or 0) >= budget.max_cost_usd:
                    raise ConflictError("预算已用尽：达到费用上限，不再派发新执行")
                if unknown and budget.max_tokens is not None:
                    # 有历史执行没有可靠计量：不能声称仍在额度内。
                    raise ConflictError("token 预算无法核实：存在未计量执行，请先核对用量再派发")
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

    async def schedule_due(
        self,
        now: datetime,
        *,
        limit: int = 20,
        unviewed_pause_hours: int | None = None,
    ) -> None:
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
                gate = await self._routine_gate(
                    session, current, now=now, pause_hours=unviewed_pause_hours
                )
                if gate is not None:
                    # 无人查看：跳过本次执行，但照常推进下一次检查点，
                    # 让"打开会话"后的下一个周期自然恢复。
                    _store(row, gate)
                    continue
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

    async def _routine_gate(
        self,
        session: AsyncSession,
        objective: Objective,
        *,
        now: datetime,
        pause_hours: int | None,
    ) -> Objective | None:
        """无人查看则跳过本次例行执行；返回推进后的 Objective，None 表示放行。

        只作用于绑定了专职 Bot 的职责：以该 Bot 的 last_viewed_at 为准，
        超过阈值（None 视为从未查看）则跳过并在 Bot 上写入可观察的
        routine_paused_reason；查看（touch）会清掉该标记。
        """
        if objective.specialist_id is None or not pause_hours or pause_hours <= 0:
            return None
        specialist_row = await session.scalar(
            select(SpecialistRow).where(
                SpecialistRow.tenant_id == objective.tenant_id,
                SpecialistRow.user_id == objective.user_id,
                SpecialistRow.specialist_id == objective.specialist_id,
            )
        )
        if specialist_row is None:
            return None
        specialist = Specialist.model_validate(specialist_row.payload)
        viewed_at = specialist.last_viewed_at
        stale_seconds = (now - viewed_at).total_seconds() if viewed_at is not None else None
        if stale_seconds is not None and stale_seconds < pause_hours * 3600:
            if specialist.routine_paused_reason is not None:
                # 查看过就放行：顺手清掉历史暂停标记（自愈）。
                specialist_row.payload = specialist.model_copy(
                    update={"routine_paused_reason": None, "updated_at": now}
                ).model_dump(mode="json", by_alias=True)
            return None
        hours = round(stale_seconds / 3600, 1) if stale_seconds is not None else None
        reason = {
            "reason": "unviewed",
            "hours_since_view": hours,
            "at": now.isoformat(),
        }
        specialist_row.payload = specialist.model_copy(
            update={"routine_paused_reason": reason, "updated_at": now}
        ).model_dump(mode="json", by_alias=True)
        return objective.model_copy(
            update={
                "next_check_at": next_check(
                    objective.schedule, after=now, ends_at=objective.ends_at
                )
            }
        )

    async def touch_specialist_viewed(
        self, tenant_id: str, user_id: str, specialist_id: str, *, now: datetime
    ) -> bool:
        """打开 Bot 会话即刷新 last_viewed_at 并清除暂停标记；不递增 revision。

        查看是高频读态操作，revision 只属于配置 CAS（并发编辑保护）。
        """
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(SpecialistRow).with_for_update().where(
                    SpecialistRow.tenant_id == tenant_id,
                    SpecialistRow.user_id == user_id,
                    SpecialistRow.specialist_id == specialist_id,
                )
            )
            if row is None:
                return False
            current = Specialist.model_validate(row.payload)
            updates: dict[str, object] = {
                "last_viewed_at": now, "routine_paused_reason": None, "updated_at": now
            }
            raw_sidebar = current.config.get("sidebar")
            sidebar = (
                type_cast(dict[str, object], raw_sidebar) if isinstance(raw_sidebar, dict) else {}
            )
            if sidebar.get("unread") is True:
                updates["config"] = {**current.config, "sidebar": {**sidebar, "unread": False}}
            updated = current.model_copy(update=updates)
            row.payload = updated.model_dump(mode="json", by_alias=True)
            row.updated_at = now
        return True

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
                duration_seconds = (
                    max(0, int((execution.finished_at - execution.created_at).total_seconds()))
                    if execution.finished_at is not None
                    else None
                )
                item = AssistantInboxItem(
                    itemId=f"notice_{execution.execution_id}",
                    objectiveId=execution.objective_id,
                    executionId=execution.execution_id,
                    kind=execution.status.value,
                    title=objective.title,
                    content=summary,
                    threadId=execution.thread_id,
                    runId=execution.run_id,
                    # 与同次 checkpoint 同口径：两条都为空才算未计量。
                    metrics=AssistantInboxMetrics(
                        tokens=usage_tokens,
                        cost_usd=usage_cost,
                        unknown_usage=(
                            not execution.skipped_no_model
                            and usage_tokens is None
                            and usage_cost is None
                        ),
                        skipped_no_model=execution.skipped_no_model,
                    ),
                    durationSeconds=duration_seconds,
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

    def _managed_event_query(self) -> Select[tuple[ManagedTaskRow, ObjectiveRow]]:
        # Join the canonical Run/session owner, never trust a display summary or ID alone.
        return (
            select(ManagedTaskRow, ObjectiveRow)
            .join(ObjectiveRow, ObjectiveRow.objective_id == ManagedTaskRow.objective_id)
            .join(
                AguiThreadBindingRow,
                and_(
                    AguiThreadBindingRow.tenant_id == ManagedTaskRow.tenant_id,
                    AguiThreadBindingRow.user_id == ManagedTaskRow.user_id,
                    AguiThreadBindingRow.thread_id == ManagedTaskRow.thread_id,
                ),
            )
            .join(
                RunRow,
                and_(
                    RunRow.tenant_id == AguiThreadBindingRow.tenant_id,
                    or_(
                        RunRow.session_id == AguiThreadBindingRow.session_id,
                        cast(AguiThreadBindingRow.payload["previous_session_ids"], JSONB).contains(
                            func.jsonb_build_array(RunRow.session_id)
                        ),
                    ),
                ),
            )
            .join(
                SessionRow,
                and_(
                    SessionRow.tenant_id == RunRow.tenant_id,
                    SessionRow.user_id == AguiThreadBindingRow.user_id,
                    SessionRow.session_id == RunRow.session_id,
                ),
            )
            .outerjoin(
                AssistantInboxRow,
                and_(
                    AssistantInboxRow.tenant_id == ManagedTaskRow.tenant_id,
                    AssistantInboxRow.user_id == ManagedTaskRow.user_id,
                    AssistantInboxRow.dedup_key
                    == (
                        literal("managed:")
                        + ManagedTaskRow.managed_id
                        + literal(":")
                        + RunRow.run_id
                    ),
                ),
            )
            .where(
                ObjectiveRow.status == ObjectiveStatus.ACTIVE.value,
                ManagedTaskRow.payload["notifyOnProgress"].as_boolean().is_(True),
                ObjectiveRow.payload["notificationMode"].as_string() != "none",
                or_(
                    ObjectiveRow.payload["endsAt"].as_string().is_(None),
                    cast(ObjectiveRow.payload["endsAt"].as_string(), DateTime(timezone=True))
                    > func.now(),
                ),
            )
        )

    async def managed_event_targets(self, event: RunEvent) -> list[tuple[ManagedTask, Objective]]:
        async with self._context() as session:
            rows = await session.execute(
                self._managed_event_query().where(
                    RunRow.tenant_id == event.tenant_id,
                    RunRow.run_id == event.run_id,
                    RunRow.session_id == event.session_id,
                    or_(
                        AssistantInboxRow.item_id.is_(None),
                        AssistantInboxRow.payload["sourceSequence"].as_integer() < event.sequence,
                    ),
                )
            )
            return [
                (ManagedTask.model_validate(task.payload), _objective(obj)) for task, obj in rows
            ]

    async def pending_managed_events(self, *, limit: int = 100) -> list[RunEvent]:
        """Repair missed fan-out from the source log and each card's consumed sequence.

        There is no global timestamp high-water mark: late commits in other Runs
        remain discoverable. One card per binding/Run coalesces bursts.
        """
        query = (
            self._managed_event_query()
            .join(
                EventRow,
                and_(
                    EventRow.tenant_id == RunRow.tenant_id,
                    EventRow.run_id == RunRow.run_id,
                ),
            )
            .where(
                EventRow.payload["type"].as_string().in_(MANAGED_PROGRESS_EVENTS),
                ~select(ObjectiveExecutionRow.execution_id)
                .where(
                    ObjectiveExecutionRow.objective_id == ManagedTaskRow.objective_id,
                    ObjectiveExecutionRow.payload["runId"].as_string() == EventRow.run_id,
                )
                .exists(),
                EventRow.timestamp
                >= func.coalesce(
                    cast(
                        ManagedTaskRow.payload["notifySince"].as_string(), DateTime(timezone=True)
                    ),
                    ManagedTaskRow.created_at,
                ),
                or_(
                    AssistantInboxRow.item_id.is_(None),
                    AssistantInboxRow.payload["sourceSequence"].as_integer() < EventRow.sequence,
                ),
            )
            .with_only_columns(EventRow)
            .order_by(EventRow.timestamp, EventRow.sequence)
            .limit(limit)
        )
        async with self._context() as session:
            rows = await session.scalars(query)
            return [RunEvent.model_validate(row.payload) for row in rows.unique()]

    async def record_managed_notice(
        self,
        task: ManagedTask,
        signal: RunEvent,
        notice: AssistantInboxItem,
    ) -> bool:
        async with self._context(write=True) as session:
            objective = _objective(
                await self._owned(
                    session, task.tenant_id, task.user_id, task.objective_id, lock=True
                )
            )
            matched = (
                await session.execute(
                    self._managed_event_query()
                    .where(
                        ManagedTaskRow.managed_id == task.managed_id,
                        RunRow.tenant_id == signal.tenant_id,
                        RunRow.run_id == signal.run_id,
                        RunRow.session_id == signal.session_id,
                    )
                    .with_for_update(of=ManagedTaskRow)
                )
            ).first()
            if matched is None or objective.notification_mode.value == "none":
                return False
            current = ManagedTask.model_validate(matched[0].payload)
            if signal.timestamp < (current.notify_since or current.created_at):
                return False
            # Objective executions already produce their own canonical notice.
            if await session.scalar(
                select(ObjectiveExecutionRow.execution_id)
                .where(
                    ObjectiveExecutionRow.objective_id == task.objective_id,
                    ObjectiveExecutionRow.payload["runId"].as_string() == signal.run_id,
                )
                .limit(1)
            ):
                return False
            key = f"managed:{task.managed_id}:{signal.run_id}"
            row = await session.scalar(
                select(AssistantInboxRow)
                .where(
                    AssistantInboxRow.tenant_id == task.tenant_id,
                    AssistantInboxRow.user_id == task.user_id,
                    AssistantInboxRow.dedup_key == key,
                )
                .with_for_update()
            )
            if row is not None:
                previous = AssistantInboxItem.model_validate(row.payload)
                if (previous.source_sequence or 0) >= (notice.source_sequence or 0):
                    return False
                # Only new material reopens the same card; duplicates do not reset read state.
                row.payload = notice.model_dump(mode="json", by_alias=True)
                row.created_at, row.read_at = notice.created_at, None
            else:
                session.add(
                    AssistantInboxRow(
                        item_id=notice.item_id,
                        objective_id=task.objective_id,
                        tenant_id=task.tenant_id,
                        user_id=task.user_id,
                        dedup_key=key,
                        created_at=notice.created_at,
                        payload=notice.model_dump(mode="json", by_alias=True),
                    )
                )
            return True

    async def configure_managed_notifications(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        managed_id: str,
        *,
        enabled: bool,
        expected_revision: int,
        now: datetime,
    ) -> ManagedTask:
        async with self._context(write=True) as session:
            row = await self._owned(session, tenant_id, user_id, objective_id, lock=True)
            current = _objective(row)
            if current.revision != expected_revision:
                raise ConflictError("事项已变更，请刷新后重试")
            if enabled and current.status.terminal:
                raise ConflictError("职责已结束，不能重新开启进展通知")
            binding = await session.scalar(
                select(ManagedTaskRow)
                .where(
                    ManagedTaskRow.managed_id == managed_id,
                    ManagedTaskRow.tenant_id == tenant_id,
                    ManagedTaskRow.user_id == user_id,
                    ManagedTaskRow.objective_id == objective_id,
                )
                .with_for_update()
            )
            if binding is None:
                raise NotFoundError("Managed task not found")
            task = ManagedTask.model_validate(binding.payload)
            updated = task.model_copy(
                update={
                    "notify_on_progress": enabled,
                    "notification_error": None,
                    "notify_since": now
                    if enabled and not task.notify_on_progress
                    else task.notify_since,
                }
            )
            binding.payload = updated.model_dump(mode="json", by_alias=True)
            _store(
                row,
                current.model_copy(update={"revision": current.revision + 1, "updated_at": now}),
            )
            _event(session, current, "managed_subscription", f"{managed_id}:{enabled}", now)
            return updated

    async def suspend_managed_notifications(self, task: ManagedTask) -> None:
        """Access revocation disables this projection; it never restarts work."""
        async with self._context(write=True) as session:
            row = await session.scalar(
                select(ManagedTaskRow)
                .where(
                    ManagedTaskRow.managed_id == task.managed_id,
                    ManagedTaskRow.tenant_id == task.tenant_id,
                    ManagedTaskRow.user_id == task.user_id,
                )
                .with_for_update()
            )
            if row is None:
                return
            current = ManagedTask.model_validate(row.payload)
            if current.notify_since != task.notify_since:
                return
            row.payload = current.model_copy(
                update={
                    "notify_on_progress": False,
                    "notification_error": "访问权限或关联资源已失效，请核对后重新开启关注。",
                }
            ).model_dump(mode="json", by_alias=True)

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
                    or_(
                        ManagedTaskRow.role == "execute",
                        ManagedTaskRow.objective_id == task.objective_id,
                    ),
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
                (item.depends_on_objective_id for item in bindings if item.depends_on_objective_id),
                None,
            )

    async def set_duty_specialist(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        specialist_id: str,
        *,
        now: datetime,
    ) -> Objective:
        async with self._context(write=True) as session:
            row = await self._owned(session, tenant_id, user_id, objective_id, lock=True)
            current = _objective(row)
            updated = Objective.model_validate(
                current.model_dump()
                | {
                    "specialist_id": specialist_id,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            _store(row, updated)
            _event(session, updated, "specialist_attached", specialist_id, now)
        return updated

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

    async def conversation_notices(
        self, tenant_id: str, user_id: str, *,
        before: tuple[datetime, str] | None = None, limit: int = 100,
    ) -> tuple[list[AssistantInboxItem], int]:
        """Page the owner's durable receipts, including ones already read.

        Share the conversation cursor's (timestamp, message id) ordering. The
        old inbox's newest-100 limit must not silently truncate chat history.
        """
        from harness.assistant.chat_notices import NOTICE_MESSAGE_PREFIX

        async with self._context() as session:
            owner = (
                AssistantInboxRow.tenant_id == tenant_id,
                AssistantInboxRow.user_id == user_id,
            )
            total = await session.scalar(
                select(func.count()).select_from(AssistantInboxRow).where(*owner)
            )
            query = select(AssistantInboxRow).where(*owner)
            if before is not None:
                at, message_id = before
                query = query.where(or_(
                    AssistantInboxRow.created_at < at,
                    and_(AssistantInboxRow.created_at == at,
                         literal(NOTICE_MESSAGE_PREFIX) + AssistantInboxRow.item_id < message_id),
                ))
            rows = await session.scalars(query.order_by(
                AssistantInboxRow.created_at.desc(), AssistantInboxRow.item_id.desc(),
            ).limit(limit))
            return [AssistantInboxItem.model_validate(row.payload).model_copy(
                update={"read_at": row.read_at}
            ) for row in rows], int(total or 0)

    async def conversation_notice(
        self, tenant_id: str, user_id: str, item_id: str,
    ) -> AssistantInboxItem | None:
        async with self._context() as session:
            row = await session.scalar(select(AssistantInboxRow).where(
                AssistantInboxRow.tenant_id == tenant_id,
                AssistantInboxRow.user_id == user_id,
                AssistantInboxRow.item_id == item_id,
            ))
            return AssistantInboxItem.model_validate(row.payload).model_copy(
                update={"read_at": row.read_at}
            ) if row else None

    async def conversation_notice_sources(
        self, tenant_id: str, user_id: str, notices: list[AssistantInboxItem],
    ) -> dict[str, dict[str, str]]:
        """Resolve source names in two bounded owner-scoped reads, not per row."""
        from harness.assistant.chat_notices import notice_href

        if not notices:
            return {}
        async with self._context() as session:
            objectives = list(await session.scalars(select(ObjectiveRow).where(
                ObjectiveRow.tenant_id == tenant_id, ObjectiveRow.user_id == user_id,
                ObjectiveRow.objective_id.in_({n.objective_id for n in notices}),
            )))
            by_objective = {o.objective_id: o.payload for o in objectives}
            ids = {n.thread_id.removeprefix("assistant_") for n in notices
                   if n.thread_id and n.thread_id.startswith("assistant_specialist_")}
            ids.update(str(o.payload["specialistId"]) for o in objectives
                       if o.payload.get("specialistId"))
            bots = list(await session.scalars(select(SpecialistRow).where(
                SpecialistRow.tenant_id == tenant_id, SpecialistRow.user_id == user_id,
                SpecialistRow.specialist_id.in_(ids),
            ))) if ids else []
            by_bot = {b.specialist_id: Specialist.model_validate(b.payload) for b in bots}
            result: dict[str, dict[str, str]] = {}
            for notice in notices:
                href = notice_href(notice)
                if not href:
                    continue
                objective = by_objective.get(notice.objective_id, {})
                sid = (notice.thread_id or "").removeprefix("assistant_")
                bot = by_bot.get(sid) or by_bot.get(str(objective.get("specialistId", "")))
                source = {"name": bot.name if bot else str(objective.get("title") or notice.title),
                          "href": href, "timestamp": notice.created_at.isoformat()}
                if bot:
                    source["botId"] = bot.specialist_id
                    source["href"] = "/assistant?" + urlencode({"bot": bot.specialist_id})
                appearance = bot.config.get("appearance") if bot else None
                if isinstance(appearance, dict):
                    source.update({key: value for key, value in appearance.items()
                                   if key in {"avatar", "color"} and isinstance(value, str)})
                result[notice.item_id] = source
            return result

    async def read_notice(
        self, tenant_id: str, user_id: str, item_id: str, now: datetime,
        *, expected_source_sequence: int | None = None,
    ) -> None:
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
            # A merged card may have changed since it was displayed. Never let
            # an acknowledgement of the old result swallow the newer one.
            if row.payload.get("sourceSequence") != expected_source_sequence:
                raise ConflictError("进展已有更新，请先查看最新结果")
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

    async def get_specialist(self, tenant_id: str, user_id: str, specialist_id: str) -> Specialist:
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

    async def list_specialists(
        self, tenant_id: str, user_id: str, *, query: str | None = None,
    ) -> list[Specialist]:
        async with self._context() as session:
            statement = select(SpecialistRow).where(
                SpecialistRow.tenant_id == tenant_id, SpecialistRow.user_id == user_id
            )
            if query:
                statement = statement.where(or_(
                    func.lower(SpecialistRow.payload["name"].as_string())
                    .contains(query.lower(), autoescape=True),
                    SpecialistRow.specialist_id == query,
                ))
            rows = await session.scalars(
                statement
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
        profile: dict[str, object] | None = None,
        now: datetime,
    ) -> Specialist:
        if sum(item is not None for item in (config, status, profile)) != 1:
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
            if profile is not None:
                # 标题/外观属于呈现信息，跟描述一起落 config；名称是身份字段，单独改。
                for key in ("name", "description"):
                    if profile.get(key) is not None:
                        changes[key] = profile[key]
                presentation = {
                    key: profile[key]
                    for key in ("title", "appearance", "sidebar")
                    if profile.get(key) is not None
                }
                if presentation:
                    merged = dict(current.config)
                    merged.update(presentation)
                    changes["config"] = merged
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

    async def desktop_occupancy(
        self, *, tenant_id: str, user_id: str, now: datetime
    ) -> dict[str, object]:
        """共享桌面的只读占用/排队投影（不改变任何执行状态）。

        holder＝当前持有人（含职责标题）；waiting＝需要桌面且仍在排队的
        pending 执行（按创建序），带中文等待原因与排队位置，供 API/UI 读。
        """
        async with self._context() as session:
            holder_row = await session.scalar(
                select(DesktopTurnRow).where(
                    DesktopTurnRow.tenant_id == tenant_id,
                    DesktopTurnRow.user_id == user_id,
                    DesktopTurnRow.released_at.is_(None),
                    DesktopTurnRow.expires_at > now,
                )
            )
            holder: dict[str, object] | None = None
            holder_execution_id: str | None = None
            if holder_row is not None:
                holder_execution_id = holder_row.execution_id
                objective_row = await session.scalar(
                    select(ObjectiveRow).where(
                        ObjectiveRow.objective_id == holder_row.objective_id,
                        ObjectiveRow.tenant_id == tenant_id,
                        ObjectiveRow.user_id == user_id,
                    )
                )
                title = (
                    str(objective_row.payload.get("title", "")) if objective_row is not None else ""
                )
                holder = {
                    "objective_id": holder_row.objective_id,
                    "execution_id": holder_row.execution_id,
                    "objective_title": title,
                    "acquired_at": holder_row.acquired_at.isoformat(),
                    "expires_at": holder_row.expires_at.isoformat(),
                }
            rows = await session.execute(
                select(ObjectiveExecutionRow, ObjectiveRow)
                .join(
                    ObjectiveRow,
                    and_(
                        ObjectiveRow.objective_id == ObjectiveExecutionRow.objective_id,
                        ObjectiveRow.tenant_id == ObjectiveExecutionRow.tenant_id,
                        ObjectiveRow.user_id == ObjectiveExecutionRow.user_id,
                    ),
                )
                .where(
                    ObjectiveExecutionRow.tenant_id == tenant_id,
                    ObjectiveExecutionRow.user_id == user_id,
                    ObjectiveExecutionRow.status == ObjectiveExecutionStatus.PENDING.value,
                )
                .order_by(ObjectiveExecutionRow.created_at)
            )
            waiting: list[dict[str, object]] = []
            for execution_row, objective_row in rows:
                budget = objective_row.payload.get("budget") or {}
                if budget.get("requiresDesktop") is not True:
                    continue
                if (
                    holder_execution_id is not None
                    and execution_row.execution_id == holder_execution_id
                ):
                    continue
                waiting.append(
                    {
                        "objective_id": execution_row.objective_id,
                        "execution_id": execution_row.execution_id,
                        "objective_title": str(objective_row.payload.get("title", "")),
                        "position": len(waiting) + 1,
                        "wait_reason": (
                            "单执行者占用中（另一项职责正在使用共享桌面）"
                            if holder_row is not None
                            else "等待桌面就绪"
                        ),
                    }
                )
            return {"holder": holder, "waiting": waiting}

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

    async def active_desktop_turn(self, tenant_id: str, user_id: str) -> DesktopTurn | None:
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
