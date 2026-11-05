"""Owned Bot lifecycle and message transport, independent of business planning."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD, primary_specialist_id
from harness.assistant.delegation import specialist_directory_instructions
from harness.assistant.models import SpecialistStatus
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay

if TYPE_CHECKING:
    from harness.application.events import EventService
    from harness.assistant.service import AssistantService
    from harness.core.models import Run, Session


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    request_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")


class CreateBotRequest(ControlRequest):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2_000)


class SendMessageRequest(ControlRequest):
    specialist_id: str = Field(min_length=1, max_length=128)
    # Omitted text means the actual user message; a chosen brief remains traceable
    # to that source. Attachments default to this turn's new uploads only.
    text: str | None = Field(default=None, min_length=1, max_length=20_000)
    input_artifact_ids: list[str] | None = Field(default=None, max_length=20)


def _result(payload: dict[str, Any], *, failed: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            **({"isError": True} if failed else {})}


class BotControlPlane:
    """Per-operation idempotency; no per-turn plan, classification or tool gate."""

    def __init__(
        self, assistant: "AssistantService", events: "EventService", run: "Run", session: "Session",
    ):
        self.assistant, self.events, self.run, self.session = assistant, events, run, session
        self.lock = asyncio.Lock()
        self.input_ids = set(run.input.get("input_artifact_ids") or [])
        inherited = set(run.input.get("inherited_input_artifact_ids") or [])
        self.new_input_ids = self.input_ids - inherited

    def _event_id(self, kind: str, request_key: str) -> str:
        key = hashlib.sha256(f"{kind}:{request_key}".encode()).hexdigest()[:32]
        return f"assistant-control-{self.run.run_id}-{key}"

    async def _record(self, kind: str, key: str, payload: dict[str, Any]) -> None:
        await self.events.append_once(
            event_id=self._event_id(kind, key), tenant_id=self.run.tenant_id,
            run_id=self.run.run_id, session_id=self.run.session_id, event_type=kind,
            timestamp=datetime.now(UTC).isoformat(), payload=payload,
        )

    async def _completed(self, kind: str, key: str) -> dict[str, Any] | None:
        return next((event.payload for event in await self.events.list_after(
            self.run.tenant_id, self.run.run_id, 0,
        ) if event.event_id == self._event_id(kind, key)), None)

    async def create_bot(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            request = CreateBotRequest.model_validate(arguments)
            async with self.lock:
                await self.assistant.validate_assistant_control_run(
                    self.run.tenant_id, self.session.user_id, self.run.run_id,
                )
                await self._record("assistant.bot.creation.requested", request.request_key, {
                    "name": request.name, "description": request.description,
                })
                prior = await self._completed("assistant.bot.created", request.request_key)
                if prior is not None:
                    await self.assistant.repository.get_specialist(
                        self.run.tenant_id, self.session.user_id, prior["specialistId"],
                    )
                    return _result(prior)
                bot, created = await self.assistant.create_specialist(
                    self.run.tenant_id, self.session.user_id, name=request.name,
                    description=request.description, agent_name=self.session.agent_name,
                    agent_version=self.session.agent_version,
                )
                payload = {"status": "created" if created else "existing",
                           "specialistId": bot.specialist_id, "name": bot.name,
                           "conversation": f"/assistant?bot={bot.specialist_id}"}
                await self._record("assistant.bot.created", request.request_key, payload)
                return _result(payload)
        except (ConflictError, NotFoundError, PermissionDeniedError, ValueError) as e:
            return _result({"status": "failed", "error": str(e)}, failed=True)

    async def send_message(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            request = SendMessageRequest.model_validate(arguments)
            ids = sorted(self.new_input_ids if request.input_artifact_ids is None
                         else set(request.input_artifact_ids))
            if not set(ids) <= self.input_ids:
                raise PermissionDeniedError("只能转交当前请求已授权的附件")
            text = (request.text if request.text is not None
                    else str(self.run.input.get("prompt", "")))
            if not text.strip() and not ids:
                raise ValueError("消息正文或附件不能为空")
            fingerprint = hashlib.sha256(json.dumps(
                {"text": text, "input_artifact_ids": ids}, ensure_ascii=False, sort_keys=True,
            ).encode()).hexdigest()
            async with self.lock:
                await self.assistant.validate_assistant_control_run(
                    self.run.tenant_id, self.session.user_id, self.run.run_id,
                )
                await self._record("assistant.message.requested", request.request_key, {
                    "specialistId": request.specialist_id, "contentHash": fingerprint,
                })
                prior = await self._completed("ui.assistant_delivery", request.request_key)
                if prior is not None:
                    return _result({"status": "delivered", **prior})
                quote = str(self.run.input.get("quoted_message_context", ""))
                if text != self.run.input.get("prompt", ""):
                    quote = f"来源请求：\n{self.run.input.get('prompt', '')}\n\n{quote}"
                receipt = await self.assistant.send_mention_message(
                    self.run.tenant_id, self.session.user_id, request.specialist_id,
                    text=text, input_artifact_ids=ids,
                    idempotency_key=f"message:{self.run.run_id}:{request.request_key}",
                    source_thread_id=PERSONAL_ASSISTANT_THREAD, dispatched=True,
                    model_route=self.run.input.get("model_route_override"),
                    quoted_message_context=quote,
                )
                bot = await self.assistant.repository.get_specialist(
                    self.run.tenant_id, self.session.user_id, receipt.specialist_id,
                )
                payload = {
                    "handoverId": receipt.handover_id, "specialistId": receipt.specialist_id,
                    "specialistName": bot.name,
                    "createdAt": (receipt.sent_at or receipt.created_at).isoformat(),
                    "sourceThreadId": receipt.source_thread_id,
                    "targetThreadId": receipt.target_thread_id, "runId": receipt.run_id,
                }
                await self._record("ui.assistant_delivery", request.request_key, payload)
                return _result({"status": "delivered", **payload})
        except (ConflictError, NotFoundError, PermissionDeniedError, ValueError) as e:
            return _result({"status": "failed", "error": str(e)}, failed=True)


async def assistant_messaging_overlay(
    assistant: "AssistantService", events: "EventService", run: "Run", session: "Session",
) -> PlatformToolOverlay:
    bots = [bot for bot in await assistant.repository.list_specialists(
        run.tenant_id, session.user_id,
    ) if bot.specialist_id != primary_specialist_id(run.tenant_id, session.user_id)
        and bot.status is not SpecialistStatus.ARCHIVED]
    controls = BotControlPlane(assistant, events, run, session)
    return PlatformToolOverlay(
        tools=(
            PlatformTool(
                name="assistant_create_specialist", description=(
                    "创建或复用本人Bot联系人，返回标识和会话入口；不自动发送消息、执行或设置周期。"
                    "同一次操作重试沿用request_key。"
                ), schema=CreateBotRequest.model_json_schema(), handler=controls.create_bot,
            ),
            PlatformTool(
                name="assistant_send_message", description=(
                    "向本人Bot会话发送消息并返回真实投递回执。"
                    "问题、确认、授权与结果会在原来源会话展示，用户可在原会话回答。"
                    "text省略时发送用户原话，附件省略时转交本轮新上传文件。"
                    "不同消息用不同request_key，同一消息重试保持key和内容不变。"
                ), schema=SendMessageRequest.model_json_schema(), handler=controls.send_message,
            ),
        ),
        instructions=specialist_directory_instructions(bots, run.tenant_id, session.user_id),
        require_public_preface=False,
    )
