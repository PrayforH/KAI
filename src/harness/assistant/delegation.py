"""Model-facing delegation tools for the personal assistant.

A long task must never hold the conversation hostage: the model parks it as an
objective with its own execution thread, keeps chatting, and reports progress
from the durable record instead of from its own memory of the run. The overlay
depends on a narrow gateway, not the whole assistant service.

Only the personal assistant coordination thread carries these tools: objective
executions run in their own threads and must not be able to delegate further
(recursion), and unrelated agents never gain delegation.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.assistant.models import (
    DelegationMode,
    Objective,
    ObjectiveAction,
    ObjectiveBrief,
    ObjectiveCheckpoint,
    ObjectiveExecution,
    ObjectiveSchedule,
    ObjectiveStatus,
)
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Run, Session
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay

INSTRUCTIONS = (
    "耗时超过一分钟的执行类任务，先调用 assistant_create_task 放到后台"
    "（它会获得独立线程并进入后台执行队列），"
    "然后立即回复用户任务已安排；期间可以继续回答其他消息。\n"
    "接管用户已有任务（用户给出任务链接/ID 或点名任务）时，"
    "先 assistant_resolve_target 解析候选，确认后 assistant_manage_work 绑定原线程，"
    "mode=follow_only 只跟进汇报，mode=autonomous 才允许代为推进。\n"
    "assistant_task_status 查询后台任务的真实执行状态、检查点与 revision；"
    "assistant_send_instruction 给托管任务补充要求（accepted=已送达当前执行，queued=进入下一轮）；"
    "assistant_update_task 需要 expected_revision，取自最近一次状态查询。\n"
    "入队只表示已安排，不代表后台已在执行；不要把简单问答包装成后台任务。"
)


class DelegationGateway(Protocol):
    """The objective operations delegation needs, nothing more."""

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        brief: ObjectiveBrief,
        *,
        idempotency_key: str | None,
        managed_thread_id: str | None = None,
        mode: str | None = None,
    ) -> Objective: ...

    async def enqueue(
        self, tenant_id: str, user_id: str, objective_id: str, *, trigger_key: str
    ) -> ObjectiveExecution: ...

    async def list_objectives(self, tenant_id: str, user_id: str) -> list[Objective]: ...

    async def executions(
        self, tenant_id: str, user_id: str, objective_id: str
    ) -> list[ObjectiveExecution]:
        """Return the objective's executions, newest first."""
        ...

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        expected_revision: int,
        action: ObjectiveAction,
    ) -> Objective: ...

    async def add_note(
        self, tenant_id: str, user_id: str, objective_id: str, *, note: str
    ) -> Objective: ...

    async def resolve_targets(
        self, tenant_id: str, user_id: str, query: str, *, limit: int = 5
    ) -> list[dict[str, Any]]: ...

    async def instruct(
        self, tenant_id: str, user_id: str, objective_id: str, text: str, *, request_id: str
    ) -> dict[str, Any]: ...

    async def checkpoints(
        self, tenant_id: str, user_id: str, objective_id: str, *, limit: int = 3
    ) -> list[Any]: ...

    async def bind_thread(
        self, tenant_id: str, user_id: str, objective_id: str, thread_id: str
    ) -> None: ...

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
        mode: str,
        message_id: str,
    ) -> dict[str, Any]: ...

    async def list_specialists(self, tenant_id: str, user_id: str) -> list[Any]: ...

    async def specialist_handovers(
        self, tenant_id: str, user_id: str, *, specialist_id: str | None = None
    ) -> list[Any]: ...


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def assistant_delegation_overlay(
    gateway: DelegationGateway,
    run: Run,
    session: Session,
    *,
    coordinator_thread: bool,
    read_only: bool = False,
) -> PlatformToolOverlay:
    if not coordinator_thread:
        # Background objective executions and unrelated agents never delegate;
        # only the personal assistant coordination thread carries these tools.
        return PlatformToolOverlay()
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
        completion_criteria = str(arguments.get("completion_criteria", ""))[:2_000]
        brief = ObjectiveBrief.model_validate(
            {
                "title": title,
                "instructions": task,
                "agentName": session.agent_name,
                "agentVersion": session.agent_version,
                "schedule": schedule.model_dump(by_alias=True) if schedule else None,
                "completionCriteria": completion_criteria,
            }
        )
        # Retried tool calls inside one run reuse the same durable objective
        # instead of piling up duplicates; the repository enforces the key.
        key_material = "\0".join((run.run_id, title, task, str(interval), completion_criteria))
        idempotency_key = (
            f"delegate:{run.run_id}:{hashlib.sha256(key_material.encode()).hexdigest()[:32]}"
        )
        try:
            objective = await gateway.create(
                tenant_id, user_id, brief, idempotency_key=idempotency_key
            )
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
                        f"后台任务已安排（排队等待后台执行）：{objective.objective_id}\n"
                        f"执行线程：{execution.thread_id}；入队不代表已在执行，"
                        "真实状态和 revision 用 assistant_task_status 查询。"
                        "请立即回复用户任务已安排到后台。"
                    ),
                }
            ]
        }

    async def add_context(arguments: dict[str, Any]) -> dict[str, Any]:
        """用户对后台任务的补充以 note 进入下一次执行的上下文。"""

        objective_id = str(arguments.get("objective_id", ""))
        note = str(arguments.get("context", "")).strip()
        if not 1 <= len(note) <= 8_000:
            return _text("补充内容需在 1–8000 字之间。")
        try:
            updated = await gateway.add_note(tenant_id, user_id, objective_id, note=note)
        except (ConflictError, NotFoundError, PermissionDeniedError) as error:
            return _text(f"补充未入档：{error}")
        return _text(
            f"已补充到任务 {updated.objective_id}；将进入下一次执行的上下文，不打断进行中的执行。"
        )

    async def resolve_target(arguments: dict[str, Any]) -> dict[str, Any]:
        """把用户点名的目标解析成可核验的候选原任务；歧义时列候选，不猜。"""
        query = str(arguments.get("query", "")).strip()
        if not 1 <= len(query) <= 200:
            return _text("目标描述需在 1–200 字之间。")
        try:
            candidates = await gateway.resolve_targets(tenant_id, user_id, query)
        except (NotFoundError, PermissionDeniedError) as error:
            return _text(f"目标解析失败：{error}")
        if not candidates:
            return _text("没有匹配的任务；请用户给出更准确的名称或任务链接。")
        lines = [
            f"- {item['title'] or item['thread_id']}"
            f"（thread={item['thread_id']}，agent={item['agent_name']}@{item['agent_version']}）"
            + (
                f"，已由 {item['managed_objective_id']} 托管"
                if item["managed_objective_id"]
                else ""
            )
            for item in candidates
        ]
        return _text(
            "候选任务（用 thread 调 assistant_manage_work 绑定）：\n" + "\n".join(lines)
        )

    async def manage_work(arguments: dict[str, Any]) -> dict[str, Any]:
        """接管既有任务：绑定原线程，只跟进或自主推进，不复制历史、不开替身。

        thread_ids 传入多个任务时建立项目协调职责：协调执行在自己的线程汇总
        纳管任务进展（只读），各任务的执行托管仍按单任务语义逐个建立。
        """
        raw_ids = arguments.get("thread_ids")
        thread_ids: list[str] = (
            [str(item).strip() for item in cast(list[object], raw_ids) if str(item).strip()][:10]
            if isinstance(raw_ids, list)
            else []
        )
        thread_id = str(arguments.get("thread_id", "")).strip()
        if not thread_id and len(thread_ids) == 1:
            thread_id = thread_ids[0]
        if not thread_id and not thread_ids:
            return _text("thread_id（或 thread_ids）必填（先用 assistant_resolve_target 解析）。")
        if thread_id and not 1 <= len(thread_id) <= 128:
            return _text("thread_id 长度不合法。")
        task = str(arguments.get("task", "")).strip() or "跟进该任务，汇报进展、结果与差距。"
        if not 1 <= len(task) <= 20_000:
            return _text("任务说明需在 1–20000 字之间。")
        mode = str(arguments.get("mode", "autonomous"))
        if mode not in ("autonomous", "follow_only"):
            return _text("mode 必须是 autonomous 或 follow_only。")
        completion_criteria = str(arguments.get("completion_criteria", ""))[:2_000]
        try:
            candidates = await gateway.resolve_targets(tenant_id, user_id, thread_id)
            match = next((item for item in candidates if item["thread_id"] == thread_id), None)
            if match is None:
                return _text("找不到该任务线程或无权访问。")
            if match["managed_objective_id"]:
                return _text(
                    f"该任务已由 {match['managed_objective_id']} 托管，不重复接管；"
                    "如需调整请用 assistant_update_task。"
                )
            brief = ObjectiveBrief.model_validate(
                {
                    "title": (
                        str(arguments.get("title", "")).strip() or match["title"] or thread_id
                    )[:120],
                    "instructions": task,
                    "agentName": match["agent_name"],
                    "agentVersion": match["agent_version"],
                    "projectId": match["project_id"],
                    "completionCriteria": completion_criteria,
                }
            )
            objective = await gateway.create(
                tenant_id,
                user_id,
                brief,
                idempotency_key=None,
                managed_thread_id=thread_id or None,
                mode=mode,
            )
            for extra in thread_ids:
                if extra and extra != thread_id:
                    await gateway.bind_thread(
                        tenant_id, user_id, objective.objective_id, extra
                    )
        except (ConflictError, NotFoundError, PermissionDeniedError, ValueError) as error:
            return _text(f"托管未能建立：{error}")
        if mode == "follow_only":
            return _text(
                f"已开始只跟进 {objective.objective_id}（线程 {thread_id}）："
                "将汇报进展与差距，不代为执行。"
            )
        try:
            execution = await gateway.enqueue(
                tenant_id,
                user_id,
                objective.objective_id,
                trigger_key=f"manage:{run.run_id}:{objective.objective_id}",
            )
        except (ConflictError, ValueError) as error:
            return _text(f"托管已建立但首次执行未安排：{error}")
        return _text(
            f"已接管原任务 {objective.objective_id}（线程 {thread_id}），"
            f"首次执行已安排（排队中，执行线程 {execution.thread_id}）。"
        )

    async def send_instruction(arguments: dict[str, Any]) -> dict[str, Any]:
        """托管任务的补充：accepted=已送达当前执行；queued=进入下一轮。"""
        objective_id = str(arguments.get("objective_id", "")).strip()
        note = str(arguments.get("context", "")).strip()
        if not 1 <= len(note) <= 8_000:
            return _text("补充内容需在 1–8000 字之间。")
        try:
            outcome = await gateway.instruct(
                tenant_id,
                user_id,
                objective_id,
                note,
                request_id=f"delegate:{run.run_id}:{objective_id}",
            )
        except (NotFoundError, PermissionDeniedError) as error:
            return _text(f"补充未入档：{error}")
        label = (
            "已送达当前执行（accepted）"
            if outcome["status"] == "accepted"
            else "已入档，进入下一次执行（queued）"
        )
        return _text(f"补充[{outcome['status']}]：{label}。{outcome['message']}")

    async def delegate_specialist(arguments: dict[str, Any]) -> dict[str, Any]:
        """按职责创建/复用专职子助理并转交；缺项不启动，回执如实。"""
        name = str(arguments.get("name", "")).strip()
        task = str(arguments.get("task", "")).strip()
        if not 1 <= len(name) <= 80:
            return _text("职责名称需在 1–80 字之间。")
        if not 1 <= len(task) <= 20_000:
            return _text("职责说明需在 1–20000 字之间。")
        mode = str(arguments.get("mode", "autonomous"))
        if mode not in ("autonomous", "follow_only"):
            return _text("mode 必须是 autonomous 或 follow_only。")
        try:
            outcome = await gateway.delegate_to_specialist(
                tenant_id,
                user_id,
                name=name,
                description=str(arguments.get("description", "")).strip(),
                agent_name=str(arguments.get("agent_name", "")).strip()
                or session.agent_name,
                agent_version=str(arguments.get("agent_version", "")).strip()
                or session.agent_version,
                task=task,
                mode=mode,
                message_id=f"delegate-specialist:{run.run_id}:{name}",
            )
        except (ConflictError, NotFoundError, PermissionDeniedError, ValueError) as error:
            return _text(f"转交未完成：{error}")
        status = str(outcome["status"])
        lines = [f"转交回执[{status}]：handover={outcome['handover_id']}"]
        lines.append(
            f"子助理：{outcome['specialist_id']}"
            + ("（新建）" if outcome["created_specialist"] else "（复用已有实例）")
        )
        objective_id = outcome.get("objective_id")
        if objective_id is not None:
            lines.append(f"职责：{objective_id}")
        raw_missing = outcome.get("missing")
        missing: list[str] = (
            [str(item) for item in cast(list[object], raw_missing)]
            if isinstance(raw_missing, list)
            else []
        )
        if missing:
            lines.append(f"待补充配置：{'、'.join(missing)}；缺项不启动。")
        if status == "applied":
            lines.append("首次执行已安排（排队中）。")
        return _text("\n".join(lines))

    async def specialist_status(_arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            specialists = await gateway.list_specialists(tenant_id, user_id)
            handovers: list[Any] = await gateway.specialist_handovers(tenant_id, user_id)
        except (NotFoundError, PermissionDeniedError) as error:
            return _text(f"子助理查询失败：{error}")
        if not specialists:
            return _text("当前没有专职子助理。")
        lines: list[str] = []
        for specialist in specialists[:10]:
            latest = next(
                (item for item in handovers if item.specialist_id == specialist.specialist_id),
                None,
            )
            label = (
                f"- [{specialist.status.value}] {specialist.name}"
                f"（{specialist.specialist_id}）revision={specialist.revision}"
            )
            if latest is not None:
                label += f" 最近转交：{latest.status.value}"
            lines.append(label)
        return _text("\n".join(lines))

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
            latest = executions[0] if executions else None
            state = latest.status.value if latest else "queued"
            summary = (latest.summary or "")[:400] if latest else ""
            label = (
                f"- [{objective.status.value}] {objective.title}"
                f"（{objective.objective_id}）revision={objective.revision}"
            )
            if objective.managed_thread_id:
                label += f" 托管线程：{objective.managed_thread_id}"
            if latest:
                label += f" 最近执行：{state}"
            try:
                checkpoints = await gateway.checkpoints(
                    tenant_id, user_id, objective.objective_id
                )
            except (NotFoundError, PermissionDeniedError):
                checkpoints = []
            if checkpoints and checkpoints[0].summary:
                label += f"\n  检查点：{checkpoints[0].summary[:300]}"
            if summary:
                label += f"\n  摘要：{summary}"
            lines.append(label)
        if lines:
            lines.append("更新任务请把上面列出的 revision 作为 expected_revision。")
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
            f"任务 {updated.objective_id} 现在是 {updated.status.value}"
            f"，revision={updated.revision}。"
            "停止当前执行与停用后续调度是同一个明确动作，不会误伤其他任务。"
        )

    if read_only:
        # 项目协调者的执行线程：只读查看纳管任务，不授予创建/推进/控制权。
        return PlatformToolOverlay(
            tools=(
                PlatformTool(
                    name="assistant_task_status",
                    description="查询纳管任务列表与最新检查点（只读）。",
                    schema={"type": "object", "properties": {}},
                    handler=task_status,
                ),
            ),
            instructions=(
                "你是项目协调职责的执行者：通过 assistant_task_status 读取纳管任务与检查点，"
                "汇总进展与差距；不得创建新任务，也不得直接推进或控制它们。"
            ),
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
                name="assistant_add_context",
                description="给后台任务追加用户的补充说明；进入下一次执行的上下文。",
                schema={
                    "type": "object",
                    "properties": {
                        "objective_id": {"type": "string"},
                        "context": {"type": "string", "maxLength": 8000},
                    },
                    "required": ["objective_id", "context"],
                },
                handler=add_context,
            ),
            PlatformTool(
                name="assistant_resolve_target",
                description="把用户点名的任务解析成候选（thread、智能体、是否已被托管）；歧义时列候选确认。",
                schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "maxLength": 200}},
                    "required": ["query"],
                },
                handler=resolve_target,
            ),
            PlatformTool(
                name="assistant_manage_work",
                description=(
                    "接管用户已有任务：绑定原线程。mode=follow_only 只跟进；autonomous 代为推进。"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string", "maxLength": 128},
                        "thread_ids": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 128},
                            "maxItems": 10,
                            "description": "多任务项目托管：主任务之外再纳管这些线程",
                        },
                        "task": {"type": "string", "maxLength": 20000},
                        "title": {"type": "string", "maxLength": 120},
                        "mode": {"type": "string", "enum": ["autonomous", "follow_only"]},
                        "completion_criteria": {"type": "string", "maxLength": 2000},
                    },
                    "required": ["thread_id"],
                },
                handler=manage_work,
            ),
            PlatformTool(
                name="assistant_send_instruction",
                description="给托管任务补充要求：运行中经引导通道送达（accepted），否则入档下一轮（queued）。",
                schema={
                    "type": "object",
                    "properties": {
                        "objective_id": {"type": "string"},
                        "context": {"type": "string", "maxLength": 8000},
                    },
                    "required": ["objective_id", "context"],
                },
                handler=send_instruction,
            ),
            PlatformTool(
                name="assistant_delegate_to_specialist",
                description="为长期可复用职责创建/复用专职子助理并转交；缺配置时不启动，回执标注缺项。",
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 80},
                        "description": {"type": "string", "maxLength": 2000},
                        "task": {"type": "string", "maxLength": 20000},
                        "mode": {"type": "string", "enum": ["autonomous", "follow_only"]},
                        "agent_name": {"type": "string"},
                        "agent_version": {"type": "string"},
                    },
                    "required": ["name", "task"],
                },
                handler=delegate_specialist,
            ),
            PlatformTool(
                name="assistant_specialist_status",
                description="查询专职子助理列表、配置状态与最近转交回执。",
                schema={"type": "object", "properties": {}},
                handler=specialist_status,
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

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        brief: ObjectiveBrief,
        *,
        idempotency_key: str | None,
        managed_thread_id: str | None = None,
        mode: str | None = None,
    ) -> Objective:
        return await self._assistant.create(
            tenant_id,
            user_id,
            brief,
            idempotency_key=idempotency_key,
            managed_thread_id=managed_thread_id,
            mode=DelegationMode(mode) if mode is not None else None,
        )

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

    async def add_note(
        self, tenant_id: str, user_id: str, objective_id: str, *, note: str
    ) -> Objective:
        return await self._assistant.repository.update(
            tenant_id,
            user_id,
            objective_id,
            expected_revision=await self._revision_of(tenant_id, user_id, objective_id),
            now=datetime.now(UTC),
            note=note,
        )

    async def _revision_of(self, tenant_id: str, user_id: str, objective_id: str) -> int:
        objective = await self._assistant.repository.get(tenant_id, user_id, objective_id)
        return objective.revision

    async def resolve_targets(
        self, tenant_id: str, user_id: str, query: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        return await self._assistant.resolve_targets(tenant_id, user_id, query, limit=limit)

    async def instruct(
        self, tenant_id: str, user_id: str, objective_id: str, text: str, *, request_id: str
    ) -> dict[str, Any]:
        return await self._assistant.instruct(
            tenant_id, user_id, objective_id, text, request_id=request_id
        )

    async def checkpoints(
        self, tenant_id: str, user_id: str, objective_id: str, *, limit: int = 3
    ) -> list[ObjectiveCheckpoint]:
        return await self._assistant.repository.checkpoints(
            tenant_id, user_id, objective_id, limit=limit
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
        mode: str,
        message_id: str,
    ) -> dict[str, Any]:
        return await self._assistant.delegate_to_specialist(
            tenant_id,
            user_id,
            name=name,
            description=description,
            agent_name=agent_name,
            agent_version=agent_version,
            task=task,
            mode=DelegationMode(mode),
            message_id=message_id,
        )

    async def bind_thread(
        self, tenant_id: str, user_id: str, objective_id: str, thread_id: str
    ) -> None:
        await self._assistant.bind_thread(
            tenant_id, user_id, objective_id, thread_id
        )

    async def list_specialists(self, tenant_id: str, user_id: str) -> list[Any]:
        return await self._assistant.repository.list_specialists(tenant_id, user_id)

    async def specialist_handovers(
        self, tenant_id: str, user_id: str, *, specialist_id: str | None = None
    ) -> list[Any]:
        return await self._assistant.repository.handovers(
            tenant_id, user_id, specialist_id=specialist_id
        )


async def objective_observer_session(assistant: Any, agui: Any, session: Session) -> bool:
    """项目协调职责的执行线程：可只读查看纳管任务，无委派权。"""

    if not session.agent_name:
        return False
    try:
        record = await agui.get_thread_record_for_session(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            session_id=session.session_id,
        )
    except NotFoundError:
        return False
    thread_id = record.thread_id
    if not thread_id.startswith("assistant_"):
        return False
    objective_id = thread_id.removeprefix("assistant_")
    try:
        bindings = await assistant.repository.managed_bindings(
            session.tenant_id, session.user_id, objective_id=objective_id
        )
    except (NotFoundError, PermissionDeniedError):
        return False
    return bool(bindings)


async def coordinator_session(agui: Any, session: Session) -> bool:
    """Whether this session runs the personal assistant coordination thread.

    Objective executions live in their own threads and must never delegate
    again; unresolved (non AG-UI) sessions are not coordinators either.
    """

    try:
        record = await agui.get_thread_record_for_session(
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            session_id=session.session_id,
        )
    except NotFoundError:
        return False
    return record.thread_id == PERSONAL_ASSISTANT_THREAD
