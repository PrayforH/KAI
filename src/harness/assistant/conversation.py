"""The personal assistant conversation, shared by Web and private channels."""

from typing import cast

from ag_ui.core import RunAgentInput, UserMessage

from harness.agui.response import final_response_text
from harness.agui.service import AguiRunService
from harness.application.agents import AgentService
from harness.application.runs import RunService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import Run
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

    async def submit(self, tenant_id: str, user_id: str, key: str, text: str) -> Run:
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
                    UserMessage(id=key, content=text),
                ],
                tools=[],
                context=[],
                forwarded_props={},
            ),
        )
        return created.run

    async def answer(self, run: Run) -> str:
        events = await self.events.list_after(run.tenant_id, run.run_id, 0)
        return final_response_text(events) or f"本次处理状态：{run.status.value}。"
