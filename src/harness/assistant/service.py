"""Assistant orchestration reuses the platform's authenticated execution pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ag_ui.core import RunAgentInput, UserMessage
from pydantic import SecretStr

from harness.agui.response import final_response_text
from harness.agui.service import AguiRunService
from harness.application.agents import AgentService
from harness.application.runs import RunService
from harness.assistant.lifecycle import next_check
from harness.assistant.models import (
    SPECIALIST_REQUIRED_CONFIG,
    DelegationMode,
    HandoverStatus,
    ManagedTask,
    ManagedTaskRole,
    ManagedTaskSource,
    Objective,
    ObjectiveBrief,
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    ObjectiveSchedule,
    ObjectiveStatus,
    Specialist,
    SpecialistHandover,
    SpecialistStatus,
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
        quotas: Any = None,
        unviewed_pause_hours: int = 24,
    ) -> None:
        self.repository = repository
        self._agents, self._auth, self._projects = agents, auth, projects
        self._agui, self._runs, self._events = agui, runs, events
        # 计量：只有配置了配额服务才可能拿到真实用量；None 时如实标记未知。
        self._quotas = quotas
        # 例行程序闸门：距上次打开 Bot 会话超过该小时数则跳过调度（可观察暂停）。
        self._unviewed_pause_hours = unviewed_pause_hours

    def with_repository(self, repository: PostgresAssistantRepository) -> AssistantService:
        return AssistantService(
            repository,
            agents=self._agents,
            auth=self._auth,
            projects=self._projects,
            agui=self._agui,
            runs=self._runs,
            events=self._events,
            quotas=self._quotas,
            unviewed_pause_hours=self._unviewed_pause_hours,
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
        specialist_id: str | None = None,
        parent_objective_id: str | None = None,
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
        if parent_objective_id is not None:
            # 新建任务授权范围：父职责必须存在、属于本人，且未超出授权数量。
            if parent_objective_id == managed_thread_id:
                raise ConflictError("无法把职责绑定到自身派生出的任务")
            parent = await self.repository.get(tenant_id, user_id, parent_objective_id)
            budget = parent.budget
            allowed = budget.max_new_tasks if budget is not None else None
            if allowed is None:
                raise ConflictError("该职责未被授权在项目内新建任务")
            if await self.repository.count_children(
                tenant_id, user_id, parent_objective_id
            ) >= allowed:
                raise ConflictError(
                    f"已达新建任务授权上限（{allowed}），请调整授权范围后再派发"
                )
        objective = await self.repository.create(
            Objective(
                **brief.model_dump(),
                parentObjectiveId=parent_objective_id,
                objectiveId=f"objective_{uuid4().hex}",
                tenantId=tenant_id,
                userId=user_id,
                managedThreadId=managed_thread_id,
                specialistId=specialist_id,
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

    async def progress_fingerprint(
        self, tenant_id: str, user_id: str, objective_id: str
    ) -> str | None:
        """纳管任务的进展指纹：标题/状态/最近更新/运行数。

        无纳管任务时返回 None——单线程职责没有可比对象，按变化驱动无从判断。
        """
        overview = await self.managed_overview(tenant_id, user_id, objective_id)
        if not overview:
            return None
        parts: list[str] = []
        for item in overview:
            summary = item.get("summary")
            parts.append(
                f"{item.get('thread_id')}:{item.get('status')}:"
                f"{(summary if isinstance(summary, str) else '')[:80]}"
            )
        signature = "|".join(parts)
        return hashlib.sha256(signature.encode("utf-8")).hexdigest()

    async def _skip_unchanged_check(
        self, objective: Objective, execution: ObjectiveExecution
    ) -> bool:
        """定时检查前的变化判定：与上一个检查点比较，无变化就不唤起模型。

        只有定时触发（schedule:）且预算开启 skipUnchanged 时适用；用户手动
        触发与 delegate/manage 触发的首次执行不受影响。
        """
        budget = objective.budget
        if budget is None or not budget.skip_unchanged:
            return False
        if not execution.trigger_key.startswith("schedule:"):
            return False
        fingerprint = await self.progress_fingerprint(
            execution.tenant_id, execution.user_id, execution.objective_id
        )
        if fingerprint is None:
            return False
        checkpoints = await self.repository.checkpoints(
            execution.tenant_id, execution.user_id, execution.objective_id, limit=1
        )
        if not checkpoints:
            return False
        previous = checkpoints[0].summary
        return f"未变化指纹 {fingerprint[:16]}" in previous

    async def managed_overview(
        self, tenant_id: str, user_id: str, objective_id: str, *, limit: int = 10
    ) -> list[dict[str, object]]:
        """成果汇总的输入：每个纳管任务的标题、最近执行状态与摘要。

        只读投影，不执行任何任务；读不到的线程按不可见处理，不猜。
        """
        overview: list[dict[str, object]] = []
        bindings = await self.repository.managed_bindings(
            tenant_id, user_id, objective_id=objective_id
        )
        for task in bindings[:limit]:
            try:
                binding = await self._agui.get_binding(
                    tenant_id=tenant_id, user_id=user_id, thread_id=task.thread_id
                )
                record = await self._agui.get_thread_record(
                    tenant_id=tenant_id, user_id=user_id, thread_id=task.thread_id
                )
                runs = await self._runs.list_for_sessions(
                    tenant_id, [binding.session_id], limit=5
                )
            except (NotFoundError, PermissionDeniedError, ConflictError):
                continue
            latest = next(
                (run for run in runs if not run.status.is_terminal), None
            ) or (runs[0] if runs else None)
            if latest is None:
                continue
            raw_prompt = latest.input.get("prompt")
            prompt = raw_prompt[:200] if isinstance(raw_prompt, str) else ""
            title: str = record.title or task.thread_id
            duration_seconds = None
            if latest.status.is_terminal:
                duration_seconds = max(
                    0, round((latest.updated_at - latest.created_at).total_seconds())
                )
            overview.append(
                {
                    "thread_id": task.thread_id,
                    "title": title,
                    "status": latest.status.value,
                    "summary": prompt,
                    "active": not latest.status.is_terminal,
                    "run_id": latest.run_id,
                    "duration_seconds": duration_seconds,
                }
            )
        return overview

    def render_managed_overview(self, overview: list[dict[str, object]]) -> str:
        if not overview:
            return ""
        lines = ["纳管任务进展（成果汇总，只读）："]
        for item in overview:
            state = "进行中" if item.get("active") else str(item.get("status", ""))
            excerpt = str(item.get("summary") or "").replace("\n", " ")[:160]
            lines.append(f"- {item.get('title')}（{state}）：{excerpt or '暂无记录'}")
        return "\n".join(lines)

    async def bind_thread(
        self, tenant_id: str, user_id: str, objective_id: str, thread_id: str
    ) -> None:
        """把既有线程纳入一个职责的纳管集合（observe 角色），依赖链服务端校验。"""
        objective = await self.repository.get(tenant_id, user_id, objective_id)
        binding = await self._agui.get_binding(
            tenant_id=tenant_id, user_id=user_id, thread_id=thread_id
        )
        if (
            objective.project_id
            and binding.project_id
            and binding.project_id != objective.project_id
        ):
            # 带上双方项目线索，避免静默失败式的模糊拒绝。
            raise ConflictError(
                "跨项目纳管被拒绝：职责属于项目"
                f"「{await self._project_label(tenant_id, user_id, objective.project_id)}」，"
                f"而线程属于项目"
                f"「{await self._project_label(tenant_id, user_id, binding.project_id)}」；"
                "需要用户明确授权该项目后才能纳管"
            )
        await self.repository.bind_managed(
            ManagedTask(
                managedId=f"managed_{uuid4().hex}",
                objectiveId=objective.objective_id,
                tenantId=tenant_id,
                userId=user_id,
                threadId=thread_id,
                projectId=binding.project_id,
                role=ManagedTaskRole.OBSERVE,
                createdAt=datetime.now(UTC),
            )
        )

    def set_cipher(self, cipher: Any) -> None:
        """凭据加密器（8.10）：值只进加密存储，模型与日志只允许出现引用。"""
        self._cipher = cipher

    async def save_specialist_credential(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        name: str,
        secret: str,
        expected_revision: int,
    ) -> Specialist:
        """专用凭据输入接口：密文入库，specialist.config 只写 credential_ref。"""
        if not 1 <= len(name) <= 64:
            raise ConflictError("凭据名称需在 1–64 字之间")
        if not secret or len(secret) > 8_000:
            raise ConflictError("凭据内容需在 1–8000 字之间")
        await self.repository.get_specialist(tenant_id, user_id, specialist_id)
        cipher = getattr(self, "_cipher", None)
        if cipher is None:
            raise ConflictError("凭据存储未配置")
        reference = f"specialist_credential_{uuid4().hex}"
        ciphertext = cipher.encrypt(
            tenant_id, user_id, reference, {"secret": SecretStr(secret)}
        )
        await self.repository.save_credential_blob(
            tenant_id,
            user_id,
            reference,
            ciphertext,
            now=datetime.now(UTC),
        )
        return await self.repository.update_specialist(
            tenant_id,
            user_id,
            specialist_id,
            expected_revision=expected_revision,
            config={f"credential_ref:{name}": reference},
            now=datetime.now(UTC),
        )

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
        await self.repository.schedule_due(
            datetime.now(UTC), unviewed_pause_hours=self._unviewed_pause_hours
        )
        for _ in range(10):
            claimed = await self.repository.claim(datetime.now(UTC))
            if claimed is None:
                break
            execution, token = claimed
            try:
                async with asyncio.timeout(45):
                    updated = await self._reconcile(execution)
                usage_tokens, usage_cost = (
                    await self._usage_for(updated, updated.run_id)
                    if updated.finished_at is not None
                    else (None, None)
                )
                await self.repository.save_execution(
                    updated,
                    token,
                    datetime.now(UTC),
                    usage_tokens=usage_tokens,
                    usage_cost=usage_cost,
                )
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

    async def _usage_for(self, execution: ObjectiveExecution, run_id: str | None) -> tuple[
        int | None, float | None
    ]:
        """一次执行的真实用量：token 与费用（费用来自 micro-USD 账本）。

        配额服务未配置或账本没有该运行的记录时返回 (None, None)——调用方据此
        标记 unknown_usage，绝不把"没测到"当成 0。
        """
        if self._quotas is None or not run_id:
            return None, None
        try:
            ledger = await self._quotas.repository.list_ledger(execution.tenant_id)
        except Exception:
            logger.exception("quota ledger read failed for %s", run_id)
            return None, None
        tokens = 0
        micro_cost = 0
        seen = False
        for entry in ledger:
            if entry.subject_id != run_id or entry.amount is None:
                continue
            if entry.resource.value == "model_tokens":
                tokens += int(entry.amount)
                seen = True
            elif entry.resource.value == "model_cost_micro_usd":
                micro_cost += int(entry.amount)
                seen = True
        if not seen:
            return None, None
        return tokens, micro_cost / 1_000_000

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
                budget = objective.budget
                if budget is not None and budget.requires_desktop:
                    turn = await self.repository.acquire_desktop_turn(
                        tenant_id=execution.tenant_id,
                        user_id=execution.user_id,
                        objective_id=execution.objective_id,
                        execution_id=execution.execution_id,
                        now=now,
                    )
                    if turn is None:
                        # 等待电脑可用：不开运行、不空耗模型，下一个租约周期再看。
                        return execution.model_copy(
                            update={"summary": "等待电脑可用（另一项职责正在使用共享桌面）"}
                        )
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
                if await self._skip_unchanged_check(objective, execution):
                    fingerprint = await self.progress_fingerprint(
                        execution.tenant_id, execution.user_id, execution.objective_id
                    )
                    return execution.model_copy(
                        update={
                            "status": ObjectiveExecutionStatus.SUCCEEDED,
                            "finished_at": now,
                            # 明确标记：这次成功没有唤起模型，用量"不适用"。
                            "skipped_no_model": True,
                            "summary": (
                                "本次定时检查未发现变化，未唤起模型。"
                                f"未变化指纹 {hash_suffix(fingerprint)}"
                            ),
                        }
                    )
                criteria = execution.brief.completion_criteria or "由用户判断事项是否完成"
                managed_section = ""
                try:
                    managed_section = self.render_managed_overview(
                        await self.managed_overview(
                            execution.tenant_id,
                            execution.user_id,
                            execution.objective_id,
                        )
                    )
                except Exception:
                    logger.exception("managed overview failed; continuing without it")
                prompt = (
                    f"持续事项：{execution.brief.title}\n\n{execution.brief.instructions}"
                    f"\n\n完成标准：{criteria}"
                    f"\n\n用户后续补充：\n{execution.context or '暂无'}"
                    + (f"\n\n{managed_section}" if managed_section else "")
                    + "\n\n请完成本次检查，报告进展、结果和需要用户决定的问题。"
                    "一次执行结束不代表持续事项结束。"
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
        if run.status.is_terminal:
            budget = objective.budget
            if budget is not None and budget.requires_desktop:
                await self.repository.release_desktop_turn(
                    tenant_id=execution.tenant_id,
                    user_id=execution.user_id,
                    execution_id=execution.execution_id,
                    now=now,
                )
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

    # ------------------------------------------------------------------
    # 持久专职子助理（8.9–8.11）：复用已发布能力模板，不引入第二套发布系统。
    # ------------------------------------------------------------------

    async def _default_agent(self, tenant_id: str, user_id: str) -> tuple[str, str]:
        """给"还没挑智能体"的新 Bot 一个可执行的默认值，不做隐性发布。"""
        for specialist in await self.repository.list_specialists(tenant_id, user_id):
            if specialist.agent_name and specialist.agent_version:
                return specialist.agent_name, specialist.agent_version
        published = await self._agents.list_for_user(tenant_id, user_id)
        for version in published:
            if version.name and version.version:
                return version.name, version.version
        raise ConflictError("还没有可用的执行智能体，请先发布一个")

    async def create_specialist(
        self,
        tenant_id: str,
        user_id: str,
        *,
        name: str,
        description: str,
        agent_name: str | None = None,
        agent_version: str | None = None,
        appearance: dict[str, str] | None = None,
    ) -> tuple[Specialist, bool]:
        # 名字（slug）是去重键：已有 Bot 直接复用它已验证的绑定，
        # 不再重查注册表——调用方给的版本可能是幻觉，旧绑定永远可用。
        slug = _specialist_slug(name)
        for item in await self.repository.list_specialists(tenant_id, user_id):
            if item.slug == slug:
                return item, False
        # 新建 Bot 不强制用户挑执行智能体：先沿用本人已有 Bot 用过的那个，
        # 再退到本人已发布的第一份；真正跑哪份在执行时仍然可见。
        if not (agent_name and agent_version):
            agent_name, agent_version = await self._default_agent(tenant_id, user_id)
        await self._agents.get_published(tenant_id, user_id, agent_name, agent_version)
        now = datetime.now(UTC)
        specialist = Specialist(
            specialistId=f"specialist_{uuid4().hex}",
            tenantId=tenant_id,
            userId=user_id,
            name=name[:80],
            slug=_specialist_slug(name),
            description=description[:2_000],
            agentName=agent_name,
            agentVersion=agent_version,
            status=SpecialistStatus.NEEDS_INPUT,
            # 外观只影响呈现，不参与运行判定；存 config 便于跨设备一致。
            config={"appearance": dict(appearance)} if appearance else {},
            # 新建即视为"正在查看"：首次例行执行不被闸门拦下。
            lastViewedAt=now,
            createdAt=now,
            updatedAt=now,
        )
        stored = await self.repository.create_specialist(specialist)
        return stored, stored.specialist_id == specialist.specialist_id

    async def update_specialist_profile(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        name: str | None,
        title: str | None,
        description: str | None,
        appearance: dict[str, str] | None = None,
        expected_revision: int,
    ) -> Specialist:
        """改名/标题/描述/外观：只动呈现，不触发配置状态重算。"""
        if name is not None and not name.strip():
            raise ConflictError("名称不能为空")
        profile: dict[str, object] = {}
        if name is not None:
            profile["name"] = name.strip()[:80]
        if description is not None:
            profile["description"] = description[:2_000]
        if title is not None:
            profile["title"] = title.strip()[:80]
        if appearance is not None:
            profile["appearance"] = appearance
        if not profile:
            raise ConflictError("没有要保存的修改")
        return await self.repository.update_specialist(
            tenant_id,
            user_id,
            specialist_id,
            expected_revision=expected_revision,
            profile=profile,
            now=datetime.now(UTC),
        )

    async def _project_label(self, tenant_id: str, user_id: str, project_id: str) -> str:
        """项目名（拿不到时回退 id）：错误文案要能对上用户看到的项目。"""
        try:
            project = await self._projects.get(
                tenant_id=tenant_id, user_id=user_id, project_id=project_id
            )
            return getattr(project, "name", None) or project_id
        except Exception:  # noqa: BLE001 - 展示信息失败不改变拒绝本身
            return project_id

    async def desktop_occupancy(
        self, tenant_id: str, user_id: str, *, now: datetime | None = None
    ) -> dict[str, object]:
        """共享桌面占用/排队的只读投影（API 可直接返回）。"""
        return await self.repository.desktop_occupancy(
            tenant_id=tenant_id, user_id=user_id, now=now or datetime.now(UTC)
        )

    async def touch_specialist_viewed(
        self, tenant_id: str, user_id: str, specialist_id: str
    ) -> bool:
        """打开 Bot 会话：刷新 last_viewed_at，例行程序闸门恢复。"""
        return await self.repository.touch_specialist_viewed(
            tenant_id, user_id, specialist_id, now=datetime.now(UTC)
        )

    async def configure_specialist(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        config: dict[str, object],
        expected_revision: int,
    ) -> Specialist:
        """配置命令落为带 revision 的结构化更新；保存后按完整集重新判定状态。"""
        if not config:
            raise ConflictError("empty specialist configuration")
        schedule = config.get("schedule_minutes")
        if schedule is not None and (
            not isinstance(schedule, int) or not 15 <= schedule <= 525_600
        ):
            raise ConflictError("schedule_minutes 必须是 15–525600 之间的整数（分钟）")
        now = datetime.now(UTC)
        updated = await self.repository.update_specialist(
            tenant_id,
            user_id,
            specialist_id,
            expected_revision=expected_revision,
            config=config,
            now=now,
        )
        missing = _specialist_missing_config(updated.config)
        target = SpecialistStatus.READY if not missing else SpecialistStatus.NEEDS_INPUT
        if updated.status not in {SpecialistStatus.ACTIVE, SpecialistStatus.PAUSED} and (
            target is not updated.status
        ):
            updated = await self.repository.update_specialist(
                tenant_id,
                user_id,
                specialist_id,
                expected_revision=updated.revision,
                status=target,
                now=now,
            )
        # 频率是职责的调度属性：保存配置必须落到绑定职责，否则 due worker
        # 永远看不到 next_check_at，界面显示"已配置"却不会跑。
        interval = updated.config.get("schedule_minutes")
        if isinstance(interval, int):
            await self._sync_duty_schedule(
                tenant_id, user_id, specialist_id, interval_minutes=interval, now=now
            )
        return updated

    async def _sync_duty_schedule(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        interval_minutes: int,
        now: datetime,
    ) -> None:
        """把 Bot 的运行频率同步到它承接的每个职责（幂等：同频率不写库）。"""
        schedule = ObjectiveSchedule(intervalMinutes=interval_minutes)
        for objective in await self.repository.list(tenant_id, user_id):
            if objective.specialist_id != specialist_id:
                continue
            if (
                objective.schedule is not None
                and objective.schedule.interval_minutes == interval_minutes
            ):
                continue
            brief = ObjectiveBrief.model_validate(
                objective.model_dump(include=set(ObjectiveBrief.model_fields))
                | {"schedule": schedule}
            )
            await self.repository.update(
                tenant_id,
                user_id,
                objective.objective_id,
                expected_revision=objective.revision,
                now=now,
                brief=brief,
            )

    async def set_specialist_status(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        status: SpecialistStatus,
        expected_revision: int,
    ) -> Specialist:
        if status is SpecialistStatus.ACTIVE:
            current = await self.repository.get_specialist(tenant_id, user_id, specialist_id)
            missing = _specialist_missing_config(current.config)
            if missing:
                raise ConflictError(f"配置缺项，不能启用：{'、'.join(missing)}")
            if current.status is not SpecialistStatus.READY:
                raise ConflictError("只有 ready 状态可以启用持续运行")
            bound = [
                item
                for item in await self.repository.list(tenant_id, user_id)
                if item.specialist_id == current.specialist_id
            ]
            if not bound:
                # 没有职责就没有调度对象：启用只会显示"运行中"却什么都不会跑。
                raise ConflictError("该子助理还没有绑定职责，先转交一项职责再启用")
        now = datetime.now(UTC)
        updated = await self.repository.update_specialist(
            tenant_id,
            user_id,
            specialist_id,
            expected_revision=expected_revision,
            status=status,
            now=now,
        )
        if status is SpecialistStatus.ACTIVE:
            # 转交时缺配置的职责从未入队；启用是用户明确的"开始持续运行"
            # 信号，给还没有任何执行的职责补一次首次执行（有则原样保留）。
            await self._start_duty_if_idle(tenant_id, user_id, specialist_id, now=now)
        return updated

    async def _start_duty_if_idle(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        now: datetime,
    ) -> None:
        """职责尚无任何执行时补一次入队；已有执行/只跟进/预算拒绝都不重复派发。"""
        for objective in await self.repository.list(tenant_id, user_id):
            if objective.specialist_id != specialist_id:
                continue
            if objective.mode != DelegationMode.AUTONOMOUS:
                continue
            if objective.status != ObjectiveStatus.ACTIVE:
                continue
            if await self.repository.executions(
                tenant_id, user_id, objective.objective_id
            ):
                continue
            try:
                await self.repository.enqueue(
                    tenant_id,
                    user_id,
                    objective.objective_id,
                    trigger_key=f"activate:{specialist_id}:{objective.objective_id}",
                    now=now,
                )
            except ConflictError as error:
                # 预算/并发闸门拒绝时保持可观察：不伪装成已开始执行。
                logger.warning(
                    "specialist activation could not enqueue %s: %s",
                    objective.objective_id,
                    error,
                )

    async def attach_duty(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        objective_id: str,
        expected_revision: int,
    ) -> Specialist:
        """把一项既有职责挂到子助理名下（职责与子助理正交，可更换承接者）。"""
        specialist = await self.repository.get_specialist(
            tenant_id, user_id, specialist_id
        )
        if specialist.revision != expected_revision:
            raise ConflictError("子助理已更新，请刷新后重试")
        objective = await self.repository.get(tenant_id, user_id, objective_id)
        if objective.specialist_id and objective.specialist_id != specialist_id:
            raise ConflictError("该职责已由其他子助理承接")
        await self.repository.set_duty_specialist(
            tenant_id, user_id, objective_id, specialist_id, now=datetime.now(UTC)
        )
        return await self.repository.get_specialist(tenant_id, user_id, specialist_id)

    async def trial_specialist(
        self,
        tenant_id: str,
        user_id: str,
        specialist_id: str,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        """首次试运行：只跑一次检查并回报真实结果；缺项与未就绪都拒绝。

        试运行成功不等于"已启用持续运行"，状态仍是 ready，由用户显式启用。
        """
        specialist = await self.repository.get_specialist(
            tenant_id, user_id, specialist_id
        )
        if specialist.revision != expected_revision:
            raise ConflictError("Subsist 配置已变化，请刷新后重试")
        missing = _specialist_missing_config(specialist.config)
        if missing:
            raise ConflictError(f"配置缺项，不能试运行：{'、'.join(missing)}")
        if specialist.status not in {SpecialistStatus.READY, SpecialistStatus.ACTIVE}:
            raise ConflictError("只有待启用或运行中的子助理可以试运行")
        objectives = [
            item
            for item in await self.repository.list(tenant_id, user_id)
            if item.specialist_id == specialist.specialist_id
        ]
        if not objectives:
            raise ConflictError("该子助理还没有绑定职责")
        objective = objectives[0]
        execution = await self.repository.enqueue(
            tenant_id,
            user_id,
            objective.objective_id,
            trigger_key=f"trial:{specialist.specialist_id}:{expected_revision}",
            now=datetime.now(UTC),
        )
        return {
            "specialistId": specialist.specialist_id,
            "objectiveId": objective.objective_id,
            "executionId": execution.execution_id,
            "status": execution.status.value,
            "scheduled": "已安排一次试运行（排队中）；结果以执行记录为准。",
        }

    async def delegate_to_specialist(
        self,
        tenant_id: str,
        user_id: str,
        *,
        name: str,
        description: str,
        agent_name: str,
        agent_version: str,
        task: str,
        mode: DelegationMode,
        message_id: str,
    ) -> dict[str, object]:
        """创建或复用专职子助理并转交职责；回执分 sent/received/pending_input。"""
        now = datetime.now(UTC)
        specialist, created = await self.create_specialist(
            tenant_id,
            user_id,
            name=name,
            description=description,
            agent_name=agent_name,
            agent_version=agent_version,
        )
        # 职责的执行绑定永远来自 Bot 自己的记录（新建时已解析并校验），
        # 调用方传来的版本只做提示，不进执行。
        agent_name, agent_version = specialist.agent_name, specialist.agent_version
        missing = _specialist_missing_config(specialist.config)
        handover = await self.repository.record_handover(
            SpecialistHandover(
                handoverId=f"handover_{uuid4().hex}",
                tenantId=tenant_id,
                userId=user_id,
                specialistId=specialist.specialist_id,
                messageId=message_id,
                summary=task[:400],
                status=HandoverStatus.SENT,
                createdAt=now,
                updatedAt=now,
            )
        )
        if not created and handover.status is not HandoverStatus.SENT:
            # 重复转交按 message 幂等：回执不重建，职责不重复创建。
            return {
                "handover_id": handover.handover_id,
                "specialist_id": specialist.specialist_id,
                "objective_id": handover.objective_id,
                "status": handover.status.value,
                "missing": missing,
                "created_specialist": False,
            }
        criteria = "由用户判断职责是否完成"
        brief = ObjectiveBrief.model_validate(
            {
                "title": name[:120] or "专职职责",
                "instructions": task,
                "agentName": agent_name,
                "agentVersion": agent_version,
                "completionCriteria": criteria,
            }
        )
        objective = await self.create(
            tenant_id,
            user_id,
            brief,
            idempotency_key=f"delegate-specialist:{handover.message_id}",
            mode=mode,
            specialist_id=specialist.specialist_id,
        )
        updated_handover = await self.repository.update_handover(
            tenant_id,
            user_id,
            handover.handover_id,
            status=HandoverStatus.RECEIVED,
            now=datetime.now(UTC),
        )
        if mode is DelegationMode.FOLLOW_ONLY or missing:
            # 缺项不启动：职责先保存为待配置，不提前调度。
            if missing:
                updated_handover = await self.repository.update_handover(
                    tenant_id,
                    user_id,
                    handover.handover_id,
                    status=HandoverStatus.PENDING_INPUT,
                    now=datetime.now(UTC),
                )
            return {
                "handover_id": updated_handover.handover_id,
                "specialist_id": specialist.specialist_id,
                "objective_id": objective.objective_id,
                "status": updated_handover.status.value,
                "missing": missing,
                "created_specialist": created,
            }
        # applied 必须对应一条真实排队中的执行：只建 objective/handover 而
        # 不入队，界面会显示"已安排"但子助理会话永远是空的。
        try:
            execution = await self.repository.enqueue(
                tenant_id,
                user_id,
                objective.objective_id,
                trigger_key=(
                    f"delegate-specialist:{handover.message_id}:{objective.objective_id}"
                ),
                now=datetime.now(UTC),
            )
        except ConflictError as error:
            # 入队被预算/并发闸门拒绝时不能假装已安排；回执保留 received。
            logger.warning(
                "specialist handover could not enqueue %s: %s",
                objective.objective_id,
                error,
            )
            return {
                "handover_id": handover.handover_id,
                "specialist_id": specialist.specialist_id,
                "objective_id": objective.objective_id,
                "status": HandoverStatus.RECEIVED.value,
                "missing": [],
                "created_specialist": created,
                "error": str(error),
            }
        await self.repository.update_handover(
            tenant_id,
            user_id,
            handover.handover_id,
            status=HandoverStatus.APPLIED,
            now=datetime.now(UTC),
        )
        return {
            "handover_id": handover.handover_id,
            "specialist_id": specialist.specialist_id,
            "objective_id": objective.objective_id,
            "status": HandoverStatus.APPLIED.value,
            "missing": [],
            "created_specialist": created,
            "execution_id": execution.execution_id,
            "thread_id": execution.thread_id,
        }


def _specialist_slug(name: str) -> str:
    """职责名称 → 查重键。

    中文名称剥掉非 ASCII 后会变成空串，不同中文职责不能因此互相"查重"：
    统一在规整结果上追加原始名称的短哈希——同名（忽略大小写与首尾空白）
    得到同一 slug，不同名称（含全部中文）得到不同 slug。
    """
    key = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"{(normalized or 'zhs')[:52]}-{digest}"


def _specialist_missing_config(config: dict[str, object]) -> list[str]:
    return [
        key
        for key in SPECIALIST_REQUIRED_CONFIG
        if not isinstance(config.get(key), (str, int)) or config.get(key) == ""
    ]


def hash_suffix(fingerprint: str | None) -> str:
    return (fingerprint or "")[:16]
