"""Model-facing delegation tools for the personal assistant.

A long task must never hold the conversation hostage: the model parks it as an
objective with its own execution thread, keeps chatting, and reports progress
from the durable record instead of from its own memory of the run. The overlay
depends on a narrow gateway, not the whole assistant service.
"""

from datetime import UTC, datetime
from typing import Any, Protocol

from harness.assistant.models import (
    Objective,
    ObjectiveAction,
    ObjectiveBrief,
    ObjectiveExecution,
    ObjectiveSchedule,
    ObjectiveStatus,
)
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Run, Session
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay

INSTRUCTIONS = (
    "耗时超过一分钟的执行类任务，先调用 assistant_create_task 放到后台"
    "（它会获得独立线程并自动开始执行），"
    "然后立即回复用户任务已开始；期间可以继续回答其他消息。\n"
    "assistant_task_status 查询后台任务的状态与结果摘要；"
    "assistant_update_task 暂停/恢复/停止任务。\n"
    "不要把简单问答包装成后台任务。"
)


class DelegationGateway(Protocol):
    """The five objective operations delegation needs, nothing more."""

    async def create(self, tenant_id: str, user_id: str, brief: ObjectiveBrief) -> Objective: ...

    async def enqueue(
        self, tenant_id: str, user_id: str, objective_id: str, *, trigger_key: str
    ) -> ObjectiveExecution: ...

    async def list_objectives(self, tenant_id: str, user_id: str) -> list[Objective]: ...

    async def executions(
        self, tenant_id: str, user_id: str, objective_id: str
    ) -> list[ObjectiveExecution]: ...

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        expected_revision: int,
        action: ObjectiveAction,
    ) -> Objective: ...


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def assistant_delegation_overlay(
    gateway: DelegationGateway, run: Run, session: Session
) -> PlatformToolOverlay:
    tenant_id, user_id = session.tenant_id, session.user_id

    async def create_task(arguments: dict[str, Any]) -> dict[str, Any]:
        task = str(arguments.get("task", "")).strip()
        if not 1 <= len(task) <= 20_000:
            return _text("任务说明需在 1–20000 字之间。")
        interval = arguments.get("interval_minutes")
        schedule = None
        if interval is not None:
            if not isinstance(interval, int) or not 15 <= interval <= 525_600:
                return _text("interval_minutes 必须是 15–525600 之间的整数（分钟）。")
            schedule = ObjectiveSchedule.model_validate({"intervalMinutes": interval})
        raw_title = str(arguments.get("title", "")).strip()
        title = (raw_title or task.splitlines()[0])[:120]
        brief = ObjectiveBrief.model_validate(
            {
                "title": title,
                "instructions": task,
                "agentName": session.agent_name,
                "agentVersion": session.agent_version,
                "schedule": schedule.model_dump(by_alias=True) if schedule else None,
                "completionCriteria": str(arguments.get("completion_criteria", ""))[:2_000],
            }
        )
        try:
            objective = await gateway.create(tenant_id, user_id, brief)
            execution = await gateway.enqueue(
                tenant_id,
                user_id,
                objective.objective_id,
                trigger_key=f"delegate:{run.run_id}:{objective.objective_id}",
            )
        except (ConflictError, PermissionDeniedError, ValueError) as error:
            return _text(f"后台任务未能创建：{error}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"后台任务已创建并开始执行：{objective.objective_id}\n"
                        f"执行线程：{execution.thread_id}；状态查询用 assistant_task_status。"
                        "请立即回复用户任务已开始。"
                    ),
                }
            ]
        }

    async def task_status(_arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            objectives = await gateway.list_objectives(tenant_id, user_id)
        except (NotFoundError, PermissionDeniedError) as error:
            return _text(f"任务查询失败：{error}")
        lines: list[str] = []
        for objective in objectives[:20]:
            if objective.status is ObjectiveStatus.CANCELLED:
                continue
            executions = await gateway.executions(tenant_id, user_id, objective.objective_id)
            latest = executions[-1] if executions else None
            state = latest.status.value if latest else "queued"
            summary = (latest.summary or "")[:400] if latest else ""
            label = f"- [{objective.status.value}] {objective.title}（{objective.objective_id}）"
            if latest:
                label += f" 最近执行：{state}"
            if summary:
                label += f"\n  摘要：{summary}"
            lines.append(label)
        body = "\n".join(lines) if lines else "当前没有后台任务。"
        return {"content": [{"type": "text", "text": body}]}

    async def update_task(arguments: dict[str, Any]) -> dict[str, Any]:
        objective_id = str(arguments.get("objective_id", ""))
        revision = arguments.get("expected_revision")
        if not isinstance(revision, int) or revision < 1:
            return _text("expected_revision 必须是正整数（先查询当前任务状态）。")
        try:
            action = ObjectiveAction(str(arguments.get("action", "")))
        except ValueError:
            return _text("action 必须是 pause/resume/complete/stop 之一。")
        try:
            updated = await gateway.update(
                tenant_id,
                user_id,
                objective_id,
                expected_revision=revision,
                action=action,
            )
        except (ConflictError, NotFoundError, PermissionDeniedError) as error:
            return _text(f"任务更新失败：{error}")
        return _text(
            f"任务 {updated.objective_id} 现在是 {updated.status.value}。"
            "停止当前执行与停用后续调度是同一个明确动作，不会误伤其他任务。"
        )

    return PlatformToolOverlay(
        tools=(
            PlatformTool(
                name="assistant_create_task",
                description=(
                    "把耗时任务放到后台：创建职责并立即开始一次独立执行，不阻塞当前对话。"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "maxLength": 20000,
                            "description": "完整任务说明",
                        },
                        "title": {
                            "type": "string",
                            "maxLength": 120,
                            "description": "可选短标题",
                        },
                        "interval_minutes": {
                            "type": "integer",
                            "minimum": 15,
                            "description": "可选：周期性重跑间隔（分钟）；缺省只执行一次",
                        },
                        "completion_criteria": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": "可选完成标准",
                        },
                    },
                    "required": ["task"],
                },
                handler=create_task,
            ),
            PlatformTool(
                name="assistant_task_status",
                description="查询后台任务列表与最近执行状态、结果摘要。",
                schema={"type": "object", "properties": {}},
                handler=task_status,
            ),
            PlatformTool(
                name="assistant_update_task",
                description="暂停/恢复/完成/停止一个后台任务。",
                schema={
                    "type": "object",
                    "properties": {
                        "objective_id": {"type": "string"},
                        "action": {
                            "type": "string",
                            "enum": ["pause", "resume", "complete", "stop"],
                        },
                        "expected_revision": {"type": "integer", "minimum": 1},
                    },
                    "required": ["objective_id", "action", "expected_revision"],
                },
                handler=update_task,
            ),
        ),
        instructions=INSTRUCTIONS,
    )


class AssistantDelegationGateway:
    """Adapts the assistant service into the narrow delegation gateway."""

    def __init__(self, assistant: Any) -> None:
        # Constructed in composition where the concrete AssistantService is
        # available; the overlay boundary above stays narrow.
        self._assistant = assistant

    async def create(self, tenant_id: str, user_id: str, brief: ObjectiveBrief) -> Objective:
        return await self._assistant.create(tenant_id, user_id, brief)

    async def enqueue(
        self, tenant_id: str, user_id: str, objective_id: str, *, trigger_key: str
    ) -> ObjectiveExecution:
        return await self._assistant.repository.enqueue(
            tenant_id, user_id, objective_id, trigger_key=trigger_key, now=datetime.now(UTC)
        )

    async def list_objectives(self, tenant_id: str, user_id: str) -> list[Objective]:
        return await self._assistant.repository.list(tenant_id, user_id)

    async def executions(
        self, tenant_id: str, user_id: str, objective_id: str
    ) -> list[ObjectiveExecution]:
        return await self._assistant.repository.executions(tenant_id, user_id, objective_id)

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        expected_revision: int,
        action: ObjectiveAction,
    ) -> Objective:
        return await self._assistant.repository.update(
            tenant_id,
            user_id,
            objective_id,
            expected_revision=expected_revision,
            now=datetime.now(UTC),
            action=action,
        )
