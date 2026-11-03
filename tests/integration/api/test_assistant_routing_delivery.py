"""Natural-message routing uses the real AG-UI delivery/history pipeline once."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
from harness.assistant.routing import assistant_routing_overlay
from harness.assistant.service import AssistantService
from harness.core.models import RunStatus
from tests.integration.api.test_agui_history_pagination import (
    FIXTURE_MANIFEST,
    HEADERS,
    _drive_turn,
)
from tests.unit.assistant.test_mention_message import _specialist


@pytest.mark.asyncio
async def test_route_reuses_pinned_bot_history_and_returns_result_only_to_origin():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        c = app.state.container
        thread = "assistant_objective_original_weather"
        await _drive_turn(c, thread, ["previous forecast"])
        before = await c.agui.get_binding(tenant_id="tenant-a", user_id="user-1", thread_id=thread)
        bot = _specialist().model_copy(
            update={
                "tenant_id": "tenant-a",
                "user_id": "user-1",
                "name": "天气提醒",
                "description": "查询天气预报",
                "agent_name": "archive-assistant-agent",
                "agent_version": "different-default",
            }
        )
        repo = AsyncMock()
        repo.list_specialists.return_value = [bot]
        repo.get_specialist.return_value = bot
        repo.list.return_value = []
        receipts = {}

        async def handovers(*_args, **_kwargs):
            return list(receipts.values())

        async def record(value):
            prior = next((r for r in receipts.values() if r.message_id == value.message_id), None)
            if prior:
                return prior
            receipts[value.handover_id] = value
            return value

        async def link(_tenant, _user, receipt_id, run_id):
            updated = receipts[receipt_id].model_copy(update={"run_id": run_id})
            receipts[receipt_id] = updated
            return updated

        repo.handovers.side_effect = handovers
        repo.record_handover.side_effect = record
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
        service.primary_profile = AsyncMock(return_value=None)
        app.state.container = replace(c, assistant=service)
        repo.conversation_notices.return_value = ([], 0)
        repo.conversation_notice_sources.return_value = {}
        parent = await c.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput(
                thread_id=MAIN,
                run_id="natural-routing",
                state={},
                tools=[],
                context=[],
                messages=[UserMessage(id="natural", content="武汉明天需要带伞吗")],
                forwarded_props={"conversationSurface": "assistant"},
            ),
        )
        active_parent = parent.run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "fencing_token": parent.run.fencing_token + 1,
            }
        )
        assert await c.worker._runs.compare_and_set(parent.run.status, active_parent)
        session = await c.sessions.get("tenant-a", active_parent.session_id)
        overlay = await assistant_routing_overlay(service, c.worker._events, active_parent, session)
        args = {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "天气职责匹配"}
        await asyncio.gather(*(overlay.tools[0].handler(args) for _ in range(2)))
        recovered = await assistant_routing_overlay(
            service, c.worker._events, active_parent, session
        )
        await recovered.tools[0].handler(args)
        assert len(receipts) == 1
        receipt = next(iter(receipts.values()))
        child = await c.runs.get("tenant-a", receipt.run_id)
        assert child.session_id == before.session_id
        assert child.input["conversation_prompts"] == ["previous forecast", "武汉明天需要带伞吗"]
        assert child.input["assistant_dispatch"] is True
        await c.worker.execute("tenant-a", child.run_id)
        child = await c.runs.get("tenant-a", child.run_id)
        assert child.status.value == "succeeded"
        runs = await c.runs.list_for_sessions("tenant-a", [before.session_id], limit=50)
        assert len(runs) == 2  # Existing turn + exactly one new child Run.
        assert before == await c.agui.get_binding(
            tenant_id="tenant-a", user_id="user-1", thread_id=thread
        )
        repo.create.assert_not_awaited()
        repo.enqueue.assert_not_awaited()

        async def projections(_tenant, _user, origin, **kwargs):
            return ([(receipt, child, bot)], 1) if origin == MAIN else ([], 0)

        repo.mention_message_runs.side_effect = projections
        body = (await client.get(f"/v1/agui/threads/{MAIN}/history", headers=HEADERS)).json()
        sources = [
            json.loads(call["function"]["arguments"])
            for msg in body["messages"]
            for call in msg.get("toolCalls") or []
            if call["function"]["name"] == "harness_notice_source"
        ]
        assert [s["direction"] for s in sources] == ["sent", "received"]
        assert all(s["name"] == "天气提醒" for s in sources)
        own = (await client.get(f"/v1/agui/threads/{thread}/history", headers=HEADERS)).json()
        assert [m["content"] for m in own["messages"] if m["role"] == "user"] == [
            "previous forecast"
        ]
        assert any(
            "主助理" in call["function"]["arguments"]
            for msg in own["messages"]
            for call in msg.get("toolCalls") or []
            if call["function"]["name"] == "harness_notice_source"
        )
        forbidden = await client.get(
            f"/v1/agui/threads/{thread}/history", headers={**HEADERS, "X-User-ID": "other-user"}
        )
        assert forbidden.status_code == 404
