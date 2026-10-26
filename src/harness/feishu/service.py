"""Private channel commands join receipt, domain change and reply in one transaction."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import exists, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from harness.application.agents import AgentService
from harness.assistant.conversation import PersonalConversation
from harness.assistant.models import Objective, ObjectiveAction, ObjectiveBrief, ObjectiveStatus
from harness.assistant.service import AssistantService
from harness.assistant.storage import ObjectiveRow
from harness.auth.service import AuthService
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import RunStatus
from harness.feishu.cards import callback_button, card, text_message
from harness.feishu.models import (
    BindingCode,
    ConnectionStatus,
    FeishuSettings,
    Incoming,
    MessageContent,
)
from harness.feishu.repository import FeishuRepository, digest
from harness.feishu.storage import BindingRow, CardActionRow, DeliveryRow, IncomingRow, ReceiverRow

HELP = (
    "直接发消息即可与我的助理聊天，与 KAI 网页保持同一段对话。\n"
    "发送「事项」查看；「新建 标题」后换行填写要求；"
    "「状态 / 执行 / 暂停 / 恢复 / 完成 / 停止 标题」；"
    "「补充 标题」后换行填写内容。新建事项默认手动执行，检查频率可在 KAI 设置。"
)
ACTIONS = {
    "暂停": "pause",
    "恢复": "resume",
    "完成": "complete",
    "停止": "stop",
    "执行": "execute",
    "状态": "show",
    "补充": "note",
}
LABELS = {
    "pause": "暂停",
    "resume": "恢复",
    "complete": "完成",
    "stop": "停止整个事项",
    "execute": "执行一次",
    "show": "查看",
    "note": "补充要求",
}
STATUS = {"active": "进行中", "paused": "已暂停", "completed": "已完成", "cancelled": "已停止"}


def is_command(content: str) -> bool:
    first = content.split(maxsplit=1)[0] if content.strip() else ""
    return first in {*ACTIONS, "事项", "新建", "帮助", "help", "/help"}


class FeishuService:
    def __init__(
        self,
        repository: FeishuRepository,
        settings: FeishuSettings,
        *,
        assistant: AssistantService,
        auth: AuthService,
        agents: AgentService,
        conversation: PersonalConversation,
    ) -> None:
        self.repository, self.settings = repository, settings
        self.assistant, self.auth, self.agents = assistant, auth, agents
        self.conversation = conversation

    async def authorize(self, tenant_id: str, user_id: str) -> None:
        user, membership = await self.auth.profile(tenant_id, user_id)
        if user.disabled or membership.role not in {"owner", "admin", "member"}:
            raise PermissionDeniedError("KAI account cannot use assistant commands")

    async def issue_code(self, tenant_id: str, user_id: str) -> BindingCode:
        if not self.settings.enabled:
            raise ConflictError("管理员尚未启用飞书连接。")
        await self.authorize(tenant_id, user_id)
        return await self.repository.issue_code(tenant_id, user_id, datetime.now(UTC))

    async def status(self, tenant_id: str, user_id: str) -> ConnectionStatus:
        async with self.repository.sessions() as session:
            binding = await self.repository.binding(session, tenant_id, user_id)
            receiver = await session.get(ReceiverRow, self.repository.app_id)
            counts: dict[str, int] = {}
            if binding:
                result = await session.execute(
                    select(DeliveryRow.status, func.count())
                    .where(DeliveryRow.binding_id == binding.binding_id)
                    .group_by(DeliveryRow.status)
                )
                counts = {state: count for state, count in result.all()}
        online = bool(
            receiver and receiver.heartbeat_at > datetime.now(UTC) - timedelta(seconds=45)
        )
        return ConnectionStatus(
            configured=self.settings.configured,
            enabled=self.settings.enabled,
            receiverOnline=online,
            receiverState=receiver.state if online and receiver else "offline",
            lastEventAt=receiver.last_event_at if receiver else None,
            bound=binding is not None,
            notifications=binding.notifications if binding else False,
            boundAt=binding.created_at if binding else None,
            pendingDeliveries=counts.get("pending", 0),
            failedDeliveries=counts.get("failed", 0) + counts.get("uncertain", 0),
        )

    async def process_once(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        async with self.repository.sessions.begin() as session:
            row = await session.scalar(
                select(IncomingRow)
                .where(
                    IncomingRow.app_id == self.repository.app_id,
                    IncomingRow.status == "pending",
                    IncomingRow.next_attempt_at <= now,
                )
                .order_by(IncomingRow.created_at, IncomingRow.incoming_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return False
            incoming = Incoming.model_validate(row.payload)
            assistant = self.assistant.with_repository(
                self.assistant.repository.with_session(session)
            )
            binding: BindingRow | None = None
            try:
                async with session.begin_nested():
                    if incoming.kind == "bind":
                        binding = await self.repository.consume_code(
                            session, incoming.actor, incoming.code_digest, now
                        )
                        await self.authorize(binding.tenant_id, binding.user_id)
                        content = text_message("已绑定你的 KAI 账号。\n" + HELP)
                    else:
                        binding = await self.repository.actor_binding(
                            session, incoming.actor, lock=True
                        )
                        if binding is None:
                            content = text_message(
                                "请先登录 KAI → 我的助理 → 飞书连接，生成绑定码，"
                                "再私聊发送「绑定 绑定码」。"
                            )
                        else:
                            await self.authorize(binding.tenant_id, binding.user_id)
                            if incoming.kind == "message" and not is_command(incoming.text):
                                # Commit receipt before model work; the row is
                                # also the durable per-message execution intent.
                                row.binding_id = binding.binding_id
                                row.status = "chat_pending"
                                row.next_attempt_at = now
                                return True
                            content = await (
                                self._action(session, assistant, binding, incoming, now)
                                if incoming.kind == "action"
                                else self._command(session, assistant, binding, incoming.text, now)
                            )
                    await self.repository.reply(
                        session,
                        key=f"reply:{row.incoming_id}",
                        actor=incoming.actor,
                        binding=binding,
                        content=content,
                        now=now,
                    )
            except (ConflictError, NotFoundError, PermissionDeniedError, ValidationError) as error:
                # Do not echo database errors, entity identifiers or a rolled-back binding.
                binding = await self.repository.actor_binding(session, incoming.actor)
                content = text_message(
                    "操作未执行：绑定码或卡片可能已过期、事项已变更，"
                    "或账号暂无权限。请在 KAI 刷新后重试。" + ("\n" + HELP if binding else "")
                )
                await self.repository.reply(
                    session,
                    key=f"reply:{row.incoming_id}",
                    actor=incoming.actor,
                    binding=binding,
                    content=content,
                    now=now,
                )
                row.error_code = type(error).__name__
            except Exception:
                # An infrastructure failure rolls back the savepoint. Bound retries
                # keep one poison event from blocking every subsequent message.
                row.attempts += 1
                row.error_code = "ProcessingUnavailable"
                row.next_attempt_at = now + timedelta(seconds=min(300, 2**row.attempts))
                if row.attempts >= 5:
                    row.status, row.finished_at = "failed", now
                return True
            row.status, row.finished_at = "done", now
            row.attempts += 1
        return True

    async def process_conversation_once(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        earlier = aliased(IncomingRow)
        async with self.repository.sessions.begin() as session:
            row = await session.scalar(
                select(IncomingRow)
                .where(
                    IncomingRow.app_id == self.repository.app_id,
                    IncomingRow.status.in_(["chat_pending", "chat_running"]),
                    IncomingRow.next_attempt_at <= now,
                    ~exists(
                        select(earlier.incoming_id).where(
                            earlier.binding_id == IncomingRow.binding_id,
                            earlier.status.in_(["chat_pending", "chat_running"]),
                            tuple_(earlier.created_at, earlier.incoming_id)
                            < tuple_(IncomingRow.created_at, IncomingRow.incoming_id),
                        )
                    ),
                )
                .order_by(IncomingRow.created_at, IncomingRow.incoming_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return False
            binding = await session.get(BindingRow, row.binding_id, with_for_update=True)
            if binding is None or binding.revoked_at or binding.app_id != self.repository.app_id:
                row.status, row.finished_at = "skipped", now
                return True
            incoming = Incoming.model_validate(row.payload)
            try:
                async with session.begin_nested():
                    await self.authorize(binding.tenant_id, binding.user_id)
                    run = (
                        await self.conversation.runs.get(binding.tenant_id, row.run_id)
                        if row.run_id
                        else await self.conversation.submit(
                            binding.tenant_id,
                            binding.user_id,
                            "feishu-chat-" + row.incoming_id,
                            incoming.text,
                        )
                    )
                    row.run_id = run.run_id
                    row.error_code = None
                    if run.status.is_terminal:
                        await self.repository.reply(
                            session,
                            key="chat-result:" + row.incoming_id,
                            actor=incoming.actor,
                            binding=binding,
                            content=text_message(await self.conversation.answer(run)),
                            now=now,
                        )
                        row.status, row.finished_at = "done", now
                    else:
                        row.status = "chat_running"
                        row.next_attempt_at = now + timedelta(seconds=1)
                        if run.status is RunStatus.WAITING_APPROVAL:
                            await self.repository.reply(
                                session,
                                key="chat-approval:" + row.incoming_id,
                                actor=incoming.actor,
                                binding=binding,
                                content=text_message(
                                    "助理正在等待你的审批，请在 KAI 的我的助理对话中确认。"
                                ),
                                now=now,
                            )
            except (NotFoundError, PermissionDeniedError, ConflictError):
                await session.refresh(row)
                row.status, row.finished_at, row.error_code = "failed", now, "ChatUnavailable"
                await self.repository.reply(
                    session,
                    key="chat-result:" + row.incoming_id,
                    actor=incoming.actor,
                    binding=binding,
                    content=text_message(
                        "这条消息暂时无法处理，请检查 KAI 中的助理配置及账号权限后重试。"
                    ),
                    now=now,
                )
            except Exception:
                await session.refresh(row)
                row.attempts += 1
                row.error_code = "ChatProcessingUnavailable"
                row.next_attempt_at = now + timedelta(seconds=min(300, 2**row.attempts))
                if row.attempts >= 5:
                    row.status, row.finished_at = "failed", now
                    await self.repository.reply(
                        session,
                        key="chat-result:" + row.incoming_id,
                        actor=incoming.actor,
                        binding=binding,
                        content=text_message("这条消息处理失败，已保留记录，请稍后重试。"),
                        now=now,
                    )
        return True

    async def _token(
        self,
        session: AsyncSession,
        binding: BindingRow,
        objective: Objective,
        action: str,
        now: datetime,
        *,
        note: str = "",
    ) -> str:
        token = secrets.token_hex(24)
        session.add(
            CardActionRow(
                digest=digest(token),
                binding_id=binding.binding_id,
                objective_id=objective.objective_id,
                expected_revision=objective.revision,
                action=action,
                payload={"note": note} if note else {},
                expires_at=now + timedelta(minutes=10),
            )
        )
        return token

    async def _detail(
        self, session: AsyncSession, binding: BindingRow, objective: Objective, now: datetime
    ) -> MessageContent:
        actions = (
            []
            if objective.status.terminal
            else (
                ["execute", "pause"] if objective.status is ObjectiveStatus.ACTIVE else ["resume"]
            )
        )
        buttons = [
            callback_button(
                LABELS[action], await self._token(session, binding, objective, action, now)
            )
            for action in actions
        ]
        return card(
            objective.title,
            f"{STATUS[objective.status]} · 版本 {objective.revision}\n"
            f"{objective.instructions[:4000]}\n\n{objective.objective_id}",
            self.settings.public_base_url,
            objective_id=objective.objective_id,
            buttons=buttons,
        )

    async def _command(
        self,
        session: AsyncSession,
        assistant: AssistantService,
        binding: BindingRow,
        content: str,
        now: datetime,
    ) -> MessageContent:
        first, _, note = content.partition("\n")
        parts = first.split(maxsplit=1)
        verb, query = parts[0], parts[1].strip() if len(parts) > 1 else ""
        if verb == "新建":
            if not query or len(query) > 120:
                return text_message("请发送「新建 标题」，标题不超过 120 字；换行后填写具体要求。")
            agent = await self.agents.ensure_user_default(binding.tenant_id, binding.user_id)
            if agent is None:
                return text_message("请先在 KAI 配置可用的默认助理，再创建事项。")
            objective = await assistant.create(
                binding.tenant_id,
                binding.user_id,
                ObjectiveBrief(
                    title=query,
                    instructions=note.strip() or query,
                    agentName=agent.name,
                    agentVersion=agent.version,
                ),
            )
            return await self._detail(session, binding, objective, now)
        if verb not in ACTIONS and verb != "事项":
            return text_message(HELP)
        action = ACTIONS.get(verb, "show")
        if action == "note" and not note.strip():
            return text_message("请发送「补充 事项标题」，换行后填写需要补充的要求。")
        objectives = await assistant.repository.list(binding.tenant_id, binding.user_id)
        matches = [
            obj
            for obj in objectives
            if (query == obj.objective_id or query in obj.title)
            if query or not obj.status.terminal
        ]
        exact = [obj for obj in matches if query in {obj.objective_id, obj.title}]
        if len(exact) == 1:
            matches = exact
        if not matches:
            return text_message("未找到对应事项。\n" + HELP)
        if len(matches) == 1:
            objective = matches[0]
            if action == "show":
                return await self._detail(session, binding, objective, now)
            token = await self._token(session, binding, objective, action, now, note=note.strip())
            return card(
                f"确认{LABELS[action]}",
                objective.title
                + ("\n" + note.strip()[:4000] if note else "")
                + "\n此操作只对当前事项版本有效，10 分钟后失效。",
                self.settings.public_base_url,
                objective_id=objective.objective_id,
                buttons=[callback_button("确认" + LABELS[action], token)],
            )
        buttons = [
            callback_button(
                f"{LABELS[action]} · {obj.title}",
                await self._token(session, binding, obj, action, now, note=note.strip()),
            )
            for obj in matches[:5]
        ]
        return card(
            "选择事项",
            f"找到 {len(matches)} 个事项，展示前 5 个。点击按钮将{LABELS[action]}对应事项；"
            "也可发送完整标题缩小范围。",
            self.settings.public_base_url,
            buttons=buttons,
        )

    async def _action(
        self,
        session: AsyncSession,
        assistant: AssistantService,
        binding: BindingRow,
        incoming: Incoming,
        now: datetime,
    ) -> MessageContent:
        action = await session.scalar(
            select(CardActionRow)
            .where(
                CardActionRow.digest == incoming.action_digest,
                CardActionRow.binding_id == binding.binding_id,
            )
            .with_for_update()
        )
        if (
            action is None
            or action.consumed_at
            or action.expires_at <= now
            or not action.objective_id
        ):
            raise ConflictError("Card is invalid or expired")
        # Hold the objective lock through revision check and command. Enqueue then
        # joins this same transaction, so a concurrent Web edit cannot slip between.
        await session.scalar(
            select(ObjectiveRow)
            .where(
                ObjectiveRow.objective_id == action.objective_id,
                ObjectiveRow.tenant_id == binding.tenant_id,
                ObjectiveRow.user_id == binding.user_id,
            )
            .with_for_update()
        )
        obj = await assistant.repository.get(
            binding.tenant_id, binding.user_id, action.objective_id
        )
        if obj.revision != action.expected_revision:
            raise ConflictError("Card refers to an older objective revision")
        if action.action == "execute":
            await assistant.validate(binding.tenant_id, binding.user_id, obj)
            await assistant.repository.enqueue(
                binding.tenant_id,
                binding.user_id,
                obj.objective_id,
                trigger_key=f"feishu:{incoming.action_digest}",
                now=now,
            )
            response = card(
                "已安排执行",
                obj.title + "\n任务会在后台执行，可在 KAI 查看进度。",
                self.settings.public_base_url,
                objective_id=obj.objective_id,
            )
        elif action.action == "show":
            response = await self._detail(session, binding, obj, now)
        else:
            obj = await assistant.repository.update(
                binding.tenant_id,
                binding.user_id,
                obj.objective_id,
                expected_revision=obj.revision,
                now=now,
                note=action.payload["note"] if action.action == "note" else None,
                action=ObjectiveAction(action.action) if action.action != "note" else None,
            )
            response = await self._detail(session, binding, obj, now)
        action.consumed_at = now
        return response
