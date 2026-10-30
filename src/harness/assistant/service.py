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
    ) -> None:
        self.repository = repository
        self._agents, self._auth, self._projects = agents, auth, projects
        self._agui, self._runs, self._events = agui, runs, events
        # 计量：只有配置了配额服务才可能拿到真实用量；None 时如实标记未知。
        self._quotas = quotas

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
            overview.append(
                {
                    "thread_id": task.thread_id,
                    "title": title,
                    "status": latest.status.value,
                    "summary": prompt,
                    "active": not latest.status.is_terminal,
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
            raise ConflictError("跨项目纳管需要用户明确授权该项目")
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
        await self.repository.schedule_due(datetime.now(UTC))
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

    async def create_specialist(
        self,
        tenant_id: str,
        user_id: str,
        *,
        name: str,
        description: str,
        agent_name: str,
        agent_version: str,
    ) -> tuple[Specialist, bool]:
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
            createdAt=now,
            updatedAt=now,
        )
        stored = await self.repository.create_specialist(specialist)
        return stored, stored.specialist_id == specialist.specialist_id

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
        updated = await self.repository.update_specialist(
            tenant_id,
            user_id,
            specialist_id,
            expected_revision=expected_revision,
            config=config,
            now=datetime.now(UTC),
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
                now=datetime.now(UTC),
            )
        return updated

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
        return await self.repository.update_specialist(
            tenant_id,
            user_id,
            specialist_id,
            expected_revision=expected_revision,
            status=status,
            now=datetime.now(UTC),
        )

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
