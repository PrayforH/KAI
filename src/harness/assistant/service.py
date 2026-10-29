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
    DelegationMode,
    ManagedTask,
    ManagedTaskSource,
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

    def with_repository(self, repository: PostgresAssistantRepository) -> AssistantService:
        return AssistantService(
            repository,
            agents=self._agents,
            auth=self._auth,
            projects=self._projects,
            agui=self._agui,
            runs=self._runs,
            events=self._events,
        )

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

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        brief: ObjectiveBrief,
        *,
        idempotency_key: str | None = None,
        managed_thread_id: str | None = None,
        mode: DelegationMode | None = None,
        source: ManagedTaskSource = ManagedTaskSource.API,
    ) -> Objective:
        await self.validate(tenant_id, user_id, brief)
        now = datetime.now(UTC)
        if brief.ends_at and brief.ends_at <= now:
            raise ConflictError("The execution period must end in the future")
        if managed_thread_id is not None:
            # 接管原会话：线程必须存在、属于该用户，且底层智能体一致，
            # 否则执行会脱离原会话的上下文与授权。
            binding = await self._agui.get_binding(
                tenant_id=tenant_id, user_id=user_id, thread_id=managed_thread_id
            )
            if (binding.agent_name, binding.agent_version) != (
                brief.agent_name,
                brief.agent_version,
            ):
                raise ConflictError("托管原任务必须使用原会话的智能体与版本")
            if brief.project_id and binding.project_id and brief.project_id != binding.project_id:
                raise ConflictError("托管原任务必须使用原会话的项目")
            if not brief.project_id and binding.project_id:
                brief = brief.model_copy(update={"project_id": binding.project_id})
        objective = await self.repository.create(
            Objective(
                **brief.model_dump(),
                objectiveId=f"objective_{uuid4().hex}",
                tenantId=tenant_id,
                userId=user_id,
                managedThreadId=managed_thread_id,
                mode=mode or DelegationMode.AUTONOMOUS,
                createdAt=now,
                updatedAt=now,
                nextCheckAt=next_check(brief.schedule, after=now, ends_at=brief.ends_at),
            ),
            idempotency_key=idempotency_key,
        )
        if managed_thread_id is not None:
            await self.repository.bind_managed(
                ManagedTask(
                    managedId=f"managed_{uuid4().hex}",
                    objectiveId=objective.objective_id,
                    tenantId=tenant_id,
                    userId=user_id,
                    threadId=managed_thread_id,
                    projectId=objective.project_id,
                    createdAt=now,
                )
            )
        return objective

    async def resolve_targets(self, tenant_id: str, user_id: str, query: str, *, limit: int = 5):
        """把自然语言目标解析成可核验的候选原任务，不猜最近一条。"""
        needle = query.strip().lower()
        bindings = await self._agui.list_bindings(
            tenant_id=tenant_id, user_id=user_id, limit=50, archived=False
        )
        scored = [
            binding
            for binding in bindings
            if not needle
            or needle in (binding.title or "").lower()
            or needle in binding.thread_id.lower()
        ]
        managed = {
            task.thread_id: task.objective_id
            for task in await self.repository.managed_bindings(tenant_id, user_id)
        }
        candidates: list[dict[str, object]] = []
        for binding in scored[:limit]:
            detailed = await self._agui.get_binding(
                tenant_id=tenant_id, user_id=user_id, thread_id=binding.thread_id
            )
            candidates.append(
                {
                    "thread_id": binding.thread_id,
                    "title": binding.title,
                    "agent_name": detailed.agent_name,
                    "agent_version": detailed.agent_version,
                    "project_id": detailed.project_id,
                    "managed_objective_id": managed.get(binding.thread_id),
                }
            )
        return candidates

    async def instruct(
        self, tenant_id: str, user_id: str, objective_id: str, text: str, *, request_id: str
    ) -> dict[str, object]:
        """运行中补充：走 steer 记录 accepted，未就绪时入档为 queued。"""
        objective = await self.repository.get(tenant_id, user_id, objective_id)
        await self.repository.update(
            tenant_id,
            user_id,
            objective_id,
            expected_revision=objective.revision,
            now=datetime.now(UTC),
            note=text,
        )
        executions = await self.repository.executions(tenant_id, user_id, objective_id)
        latest = executions[0] if executions else None
        active_run_id = (
            latest.run_id
            if latest is not None
            and latest.status is ObjectiveExecutionStatus.RUNNING
            and latest.run_id
            else None
        )
        if active_run_id is not None:
            try:
                await self._runs.steer(tenant_id, active_run_id, request_id, text)
            except (ConflictError, NotFoundError):
                return {
                    "status": "queued",
                    "run_id": active_run_id,
                    "message": "当前执行未就绪或已结束；补充已入档，将进入下一次执行的上下文。",
                }
            return {
                "status": "accepted",
                "run_id": active_run_id,
                "message": "补充已提交到当前运行的引导通道，最终送达以执行记录为准。",
            }
        return {
            "status": "queued",
            "message": "当前没有进行中的执行；补充已入档，将进入下一次执行的上下文。",
        }

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
                if objective.managed_thread_id == execution.thread_id:
                    # 托管派发与用户直聊共用原线程：存在未终结运行时不并发启动，
                    # 执行保持 pending，由下一个租约周期接续。
                    binding = await self._agui.get_binding(
                        tenant_id=execution.tenant_id,
                        user_id=execution.user_id,
                        thread_id=execution.thread_id,
                    )
                    session_runs = await self._runs.list_for_sessions(
                        execution.tenant_id, [binding.session_id], limit=10
                    )
                    if any(not item.status.is_terminal for item in session_runs):
                        return execution
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
