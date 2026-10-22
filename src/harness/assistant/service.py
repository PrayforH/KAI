"""Assistant orchestration reuses the platform's authenticated execution pipeline."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from ag_ui.core import RunAgentInput, UserMessage

from harness.agui.response import final_response_text
from harness.agui.service import AguiRunService
from harness.application.agents import AgentService
from harness.application.runs import RunService
from harness.assistant.lifecycle import next_check
from harness.assistant.models import (
    Objective,
    ObjectiveBrief,
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    ObjectiveStatus,
)
from harness.assistant.repository import PostgresAssistantRepository
from harness.auth.service import AuthService
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Run, RunStatus
from harness.core.ports import EventRepository
from harness.projects.service import ProjectService

logger = logging.getLogger(__name__)


class AssistantService:
    def __init__(
        self,
        repository: PostgresAssistantRepository,
        *,
        agents: AgentService,
        auth: AuthService,
        projects: ProjectService,
        agui: AguiRunService,
        runs: RunService,
        events: EventRepository,
    ) -> None:
        self.repository = repository
        self._agents, self._auth, self._projects = agents, auth, projects
        self._agui, self._runs, self._events = agui, runs, events

    async def validate(self, tenant_id: str, user_id: str, brief: ObjectiveBrief) -> None:
        user, membership = await self._auth.profile(tenant_id, user_id)
        if user.disabled or membership.role not in {"owner", "admin", "member"}:
            raise PermissionDeniedError("Objective owner cannot execute tasks")
        try:
            await self._agents.get_published(
                tenant_id, user_id, brief.agent_name, brief.agent_version
            )
        except ConflictError as error:
            raise PermissionDeniedError("The selected agent is no longer published") from error
        if brief.project_id:
            await self._projects.get(
                tenant_id=tenant_id, user_id=user_id, project_id=brief.project_id
            )

    async def create(self, tenant_id: str, user_id: str, brief: ObjectiveBrief) -> Objective:
        await self.validate(tenant_id, user_id, brief)
        now = datetime.now(UTC)
        if brief.ends_at and brief.ends_at <= now:
            raise ConflictError("The execution period must end in the future")
        return await self.repository.create(
            Objective(
                **brief.model_dump(),
                objectiveId=f"objective_{uuid4().hex}",
                tenantId=tenant_id,
                userId=user_id,
                createdAt=now,
                updatedAt=now,
                nextCheckAt=next_check(brief.schedule, after=now, ends_at=brief.ends_at),
            )
        )

    async def process_once(self) -> None:
        await self.repository.schedule_due(datetime.now(UTC))
        for _ in range(10):
            claimed = await self.repository.claim(datetime.now(UTC))
            if claimed is None:
                break
            execution, token = claimed
            try:
                async with asyncio.timeout(45):
                    updated = await self._reconcile(execution)
                await self.repository.save_execution(updated, token, datetime.now(UTC))
            except (NotFoundError, PermissionDeniedError) as error:
                # Revocation never starts another run; an existing run is cancelled too.
                recovered = await self._recover(execution)
                if recovered and not recovered.status.is_terminal:
                    await self._runs.cancel(execution.tenant_id, recovered.run_id)
                await self.repository.save_execution(
                    execution.model_copy(
                        update={
                            "status": ObjectiveExecutionStatus.FAILED,
                            "error_code": type(error).__name__,
                            "summary": "执行权限或关联资源已失效。",
                            "finished_at": datetime.now(UTC),
                        }
                    ),
                    token,
                    datetime.now(UTC),
                )
            except Exception:
                # Durable intent plus the run idempotency key allows retry after crashes,
                # including a crash between successful submission and linking the run.
                logger.exception("assistant execution retry: %s", execution.execution_id)

    async def _recover(self, execution: ObjectiveExecution) -> Run | None:
        if execution.run_id:
            return await self._runs.get(execution.tenant_id, execution.run_id)
        try:
            binding = await self._agui.get_binding(
                tenant_id=execution.tenant_id,
                user_id=execution.user_id,
                thread_id=execution.thread_id,
            )
        except NotFoundError:
            return None
        return await self._runs.find_by_idempotency_key(
            execution.tenant_id, binding.session_id, execution.execution_id
        )

    async def _reconcile(self, execution: ObjectiveExecution) -> ObjectiveExecution:
        objective = await self.repository.get(
            execution.tenant_id, execution.user_id, execution.objective_id
        )
        run = await self._recover(execution)
        now = datetime.now(UTC)
        inactive = objective.status is not ObjectiveStatus.ACTIVE or (
            objective.ends_at is not None and objective.ends_at <= now
        )
        if inactive:
            if (
                run
                and not run.status.is_terminal
                and objective.status is not ObjectiveStatus.PAUSED
            ):
                run = await self._runs.cancel(execution.tenant_id, run.run_id)
            if run is None:
                return execution.model_copy(
                    update={
                        "status": ObjectiveExecutionStatus.CANCELLED,
                        "finished_at": now,
                        "summary": "事项已暂停或结束，本次执行未启动。",
                    }
                )
        else:
            await self.validate(execution.tenant_id, execution.user_id, execution.brief)
            if run is None:
                criteria = execution.brief.completion_criteria or "由用户判断事项是否完成"
                prompt = (
                    f"持续事项：{execution.brief.title}\n\n{execution.brief.instructions}"
                    f"\n\n完成标准：{criteria}"
                    f"\n\n用户后续补充：\n{execution.context or '暂无'}"
                    "\n\n请完成本次检查，报告进展、结果和需要用户决定的问题。一次执行结束不代表持续事项结束。"
                )
                creation = await self._agui.create_run_with_result(
                    tenant_id=execution.tenant_id,
                    user_id=execution.user_id,
                    agent_name=execution.brief.agent_name,
                    agent_version=execution.brief.agent_version,
                    project_id=execution.brief.project_id,
                    connection_mode="service_owned",
                    request=RunAgentInput(
                        thread_id=execution.thread_id,
                        run_id=execution.execution_id,
                        state={},
                        messages=[UserMessage(id=execution.execution_id, content=prompt)],
                        tools=[],
                        context=[],
                        forwarded_props={},
                    ),
                )
                run = creation.run
        assert run is not None
        changes: dict[str, object] = {
            "run_id": run.run_id,
            "status": ObjectiveExecutionStatus.RUNNING,
        }
        if run.status.is_terminal:
            status = (
                ObjectiveExecutionStatus.SUCCEEDED
                if run.status is RunStatus.SUCCEEDED
                else ObjectiveExecutionStatus.CANCELLED
                if run.status is RunStatus.CANCELLED
                else ObjectiveExecutionStatus.FAILED
            )
            events = await self._events.list_after(execution.tenant_id, run.run_id, 0)
            changes.update(
                status=status,
                finished_at=now,
                summary=final_response_text(events)[-12_000:]
                or f"本次执行状态：{run.status.value}",
            )
        return ObjectiveExecution.model_validate(execution.model_dump() | changes)
