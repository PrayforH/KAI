"""The personal assistant conversation, shared by Web and private channels."""

from ag_ui.core import RunAgentInput, UserMessage

from harness.agui.response import final_response_text
from harness.agui.service import AguiRunService
from harness.application.agents import AgentService
from harness.application.runs import RunService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import Run
from harness.core.ports import EventRepository

PERSONAL_ASSISTANT_THREAD = "kai-personal-assistant-v1"


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
                messages=[UserMessage(id=key, content=text)],
                tools=[],
                context=[],
                forwarded_props={},
            ),
        )
        return created.run

    async def answer(self, run: Run) -> str:
        events = await self.events.list_after(run.tenant_id, run.run_id, 0)
        return final_response_text(events) or f"本次处理状态：{run.status.value}。"
