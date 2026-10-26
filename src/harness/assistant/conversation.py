"""The personal assistant conversation, shared by Web and private channels."""

from collections.abc import Sequence
from typing import cast

from ag_ui.core import (
    AudioInputContent,
    DocumentInputContent,
    ImageInputContent,
    InputContentDataSource,
    RunAgentInput,
    TextInputContent,
    UserMessage,
)

from harness.agui.response import final_response_text
from harness.agui.service import AguiRunService
from harness.application.agents import AgentService
from harness.application.runs import RunService
from harness.assistant.controls import CONTROL_HELP, RUN_LABELS, ControlIntent
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import InputArtifact, Run
from harness.core.ports import EventRepository

PERSONAL_ASSISTANT_THREAD = "kai-personal-assistant-v1"


def _previous_prompts(runs: list[Run]) -> list[str]:
    ordered = sorted(runs, key=lambda run: (run.created_at, run.run_id))
    if not ordered:
        return []
    latest = ordered[-1]
    raw = latest.input.get("conversation_prompts")
    prompts = cast(list[object], raw) if isinstance(raw, list) else []
    if (
        prompts
        and all(isinstance(prompt, str) for prompt in prompts)
        and prompts[-1] == latest.input.get("prompt")
    ):
        return list(cast(list[str], prompts))
    return [str(run.input["prompt"]) for run in ordered if run.input.get("prompt")]


class PersonalConversation:
    def __init__(
        self,
        *,
        agui: AguiRunService,
        runs: RunService,
        agents: AgentService,
        events: EventRepository,
    ) -> None:
        self.agui, self.runs, self.agents, self.events = agui, runs, agents, events

    async def submit(
        self,
        tenant_id: str,
        user_id: str,
        key: str,
        text: str,
        *,
        artifacts: Sequence[InputArtifact] = (),
        model_route: str | None = None,
    ) -> Run:
        coordinates = dict(
            tenant_id=tenant_id, user_id=user_id, thread_id=PERSONAL_ASSISTANT_THREAD
        )
        try:
            record = await self.agui.get_thread_record(**coordinates)
        except NotFoundError:
            record = None
        previous: list[str] = []
        if record:
            # Recover a submission even if a process stopped before linking it,
            # or the user switched Agent versions and retained earlier sessions.
            for session_id in reversed(record.session_ids):
                run = await self.runs.find_by_idempotency_key(tenant_id, session_id, key)
                if run is not None:
                    return run
            binding = await self.agui.get_binding(**coordinates)
            name, version = binding.agent_name, binding.agent_version
            owner, space = binding.agent_owner_user_id, binding.space_id
            previous = _previous_prompts(
                await self.runs.list_for_sessions(tenant_id, list(record.session_ids), limit=200)
            )
        else:
            agent = await self.agents.ensure_user_default(tenant_id, user_id)
            if agent is None:
                raise ConflictError("No personal assistant is configured")
            name, version, owner, space = agent.name, agent.version, user_id, None
        await self.agents.get_published(tenant_id, owner or user_id, name, version)
        created = await self.agui.create_run_with_result(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=name,
            agent_version=version,
            agent_owner_user_id=owner,
            space_id=space,
            connection_mode="service_owned",
            # Distinct external messages must retain their own durable key,
            # including two identical greetings while another turn is active.
            deduplicate_active_input=False,
            request=RunAgentInput(
                thread_id=PERSONAL_ASSISTANT_THREAD,
                run_id=key,
                state={},
                # AG-UI uses the user-message path to distinguish an append
                # from editing an earlier turn. A one-message request would
                # hide every preceding Web turn as a replaced branch.
                messages=[
                    *[
                        UserMessage(id=f"{key}-previous-{index}", content=prompt)
                        for index, prompt in enumerate(previous)
                    ],
                    UserMessage(
                        id=key,
                        content=[
                            TextInputContent(text=text),
                            *[
                                (
                                    ImageInputContent
                                    if artifact.media_type.startswith("image/")
                                    else AudioInputContent
                                    if artifact.media_type.startswith("audio/")
                                    else DocumentInputContent
                                )(
                                    source=InputContentDataSource(
                                        value=artifact.input_artifact_id,
                                        mime_type=artifact.media_type,
                                    ),
                                    metadata={
                                        "inputArtifactId": artifact.input_artifact_id,
                                        "filename": artifact.name,
                                    },
                                )
                                for artifact in artifacts
                            ],
                        ]
                        if artifacts
                        else text,
                    ),
                ],
                tools=[],
                context=[],
                forwarded_props={"modelRoute": model_route} if model_route else {},
            ),
        )
        return created.run

    async def answer(self, run: Run) -> str:
        events = await self.events.list_after(run.tenant_id, run.run_id, 0)
        return final_response_text(events) or f"本次处理状态：{run.status.value}。"

    async def plan_control(
        self, tenant_id: str, user_id: str, intent: ControlIntent
    ) -> ControlIntent:
        """Persist this target before applying a mutation; retries cannot hit a newer Run."""
        if intent.action == "help":
            return intent
        try:
            record = await self.agui.get_thread_record(
                tenant_id=tenant_id, user_id=user_id, thread_id=PERSONAL_ASSISTANT_THREAD
            )
        except NotFoundError:
            return intent
        runs = await self.runs.list_for_sessions(tenant_id, list(record.session_ids), limit=200)
        active = [run for run in runs if not run.status.is_terminal]
        candidates = active or (runs if intent.action == "status" else [])
        target = max(candidates, key=lambda run: (run.created_at, run.run_id), default=None)
        return intent.model_copy(update={"target_run_id": target.run_id if target else None})

    async def execute_control(
        self, tenant_id: str, user_id: str, intent: ControlIntent, request_id: str
    ) -> str:
        if intent.action == "help":
            return CONTROL_HELP
        if intent.action == "steer" and len(intent.text) > 8000:
            return "补充内容过长，请缩短至 8000 字以内后重发。"
        if not intent.target_run_id:
            return (
                "当前没有正在执行的任务。"
                if intent.action != "steer"
                else ("当前没有正在执行的任务，请直接发送新要求。")
            )
        record = await self.agui.get_thread_record(
            tenant_id=tenant_id, user_id=user_id, thread_id=PERSONAL_ASSISTANT_THREAD
        )
        run = await self.runs.get(tenant_id, intent.target_run_id)
        if run.tenant_id != tenant_id or run.session_id not in record.session_ids:
            raise PermissionDeniedError("Control target is outside the owned assistant thread")
        label = RUN_LABELS[run.status.value]
        if intent.action == "status":
            return f"本轮状态：{label}。"
        if run.status.is_terminal:
            return f"本轮已结束（{label}），请直接发送下一条要求。"
        if intent.action == "stop":
            stopped = await self.runs.cancel(tenant_id, run.run_id)
            return (
                "已停止当前执行。"
                if stopped.status.value == "cancelled"
                else (
                    "已请求停止，正在等待执行器退出。"
                    if not stopped.status.is_terminal
                    else f"本轮已结束（{RUN_LABELS[stopped.status.value]}）。"
                )
            )
        try:
            receipt = await self.runs.steer(tenant_id, run.run_id, request_id, intent.text)
        except ConflictError:
            return "当前执行暂不支持中途补充，请等本轮结束后直接发送新要求。"
        return (
            "补充已被执行器接收。"
            if receipt.get("status") == "accepted"
            else "补充未能送达，请等本轮结束后重新发送。"
            if receipt.get("status") == "failed"
            else "补充已提交，等待执行器接收。"
        )
