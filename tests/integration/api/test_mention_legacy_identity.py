"""A Bot default must not replace an existing thread's pinned execution identity."""

from unittest.mock import AsyncMock

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
from harness.assistant.service import AssistantService
from harness.core.errors import ConflictError
from tests.integration.api.test_agui_history_pagination import (
    FIXTURE_MANIFEST,
    HEADERS,
    _drive_turn,
)
from tests.unit.assistant.test_mention_message import _specialist


@pytest.mark.asyncio
async def test_mention_resumes_real_legacy_binding_and_keeps_history_and_guard():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        c = app.state.container
        thread = "assistant_objective_legacy"
        previous = await _drive_turn(c, thread, ["original history"])
        before = await c.agui.get_binding(tenant_id="tenant-a", user_id="user-1", thread_id=thread)
        repo = AsyncMock()
        bot = _specialist().model_copy(
            update={
                "tenant_id": "tenant-a",
                "user_id": "user-1",
                "agent_name": "archive-assistant-agent",
                "agent_version": "different-default",
            }
        )
        repo.get_specialist.return_value = bot
        repo.handovers.return_value = []
        repo.record_handover.side_effect = lambda value: value

        async def link(_tenant, _user, _id, run_id):
            return repo.record_handover.call_args.args[0].model_copy(update={"run_id": run_id})

        repo.link_mention_run.side_effect = link
        service = AssistantService(
            repo,
            agents=AsyncMock(),
            auth=AsyncMock(),
            projects=AsyncMock(),
            agui=c.agui,
            runs=c.runs,
            events=c.events,
        )
        service.specialist_thread = AsyncMock(return_value=thread)
        from harness.api.dependencies import Identity
        from harness.assistant.api import get_specialist

        repo.list.return_value = []
        detail = await get_specialist(
            bot.specialist_id, Identity(tenant_id="tenant-a", user_id="user-1"), service
        )
        assert detail["agentName"] == "echo-agent" and detail["agentVersion"] == "0.1.0"
        assert detail["specialist"].agent_name == "archive-assistant-agent"
        receipt = await service.send_mention_message(
            "tenant-a",
            "user-1",
            bot.specialist_id,
            text="new message",
            source_thread_id=MAIN,
            idempotency_key="legacy-mention-key",
        )
        await c.worker.execute("tenant-a", receipt.run_id)
        after = await c.agui.get_binding(tenant_id="tenant-a", user_id="user-1", thread_id=thread)
        assert before == after  # No replacement Session or rebinding.
        history = (await client.get(f"/v1/agui/threads/{thread}/history", headers=HEADERS)).json()
        texts = [m["content"] for m in history["messages"] if m["role"] == "user"]
        assert texts == ["original history", "new message"]
        assert previous != receipt.run_id
        retry = await c.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            append_to_history=True,
            request=RunAgentInput(
                thread_id=thread,
                run_id=f"mention-{receipt.handover_id}",
                state={},
                messages=[UserMessage(id=receipt.handover_id, content="new message")],
                tools=[],
                context=[],
                forwarded_props={},
            ),
        )
        assert retry.reused and retry.run.run_id == receipt.run_id
        assert retry.run.input["conversation_prompts"] == ["original history", "new message"]
        with pytest.raises(ConflictError, match="already bound"):
            await c.agui.create_run_with_result(
                tenant_id="tenant-a",
                user_id="user-1",
                agent_name=bot.agent_name,
                agent_version=bot.agent_version,
                request=RunAgentInput(
                    thread_id=thread,
                    run_id="wrong-identity",
                    state={},
                    messages=[UserMessage(id="bad", content="bad")],
                    tools=[],
                    context=[],
                    forwarded_props={},
                ),
            )
