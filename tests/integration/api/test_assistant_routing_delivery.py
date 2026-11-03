"""Natural-message routing uses the real AG-UI delivery/history pipeline once."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from ag_ui.core import (
    DocumentInputContent,
    InputContentDataSource,
    RunAgentInput,
    TextInputContent,
    UserMessage,
)
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
from harness.assistant.messaging import assistant_messaging_overlay
from harness.assistant.service import AssistantService, _specialist_slug
from harness.core.models import RunStatus
from tests.integration.api.test_agui_history_pagination import (
    FIXTURE_MANIFEST,
    HEADERS,
    _drive_turn,
)
from tests.unit.assistant.test_mention_message import _specialist


@pytest.mark.asyncio
@pytest.mark.parametrize("creation,attached", [(False, False), (True, True)])
async def test_messages_reuse_pinned_history_and_return_only_to_origin(creation, attached):
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
                "slug": _specialist_slug("天气提醒"),
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
            event_service=c.worker._events,
        )
        service.specialist_thread = AsyncMock(return_value=thread)
        service.primary_profile = AsyncMock(return_value=None)
        app.state.container = replace(c, assistant=service)
        repo.conversation_notices.return_value = ([], 0)
        repo.conversation_notice_sources.return_value = {}
        content = "武汉明天需要带伞吗"
        artifact_id = None
        if attached:
            upload = await client.post(
                "/v1/input-artifacts",
                headers=HEADERS,
                files={"file": ("locations.txt", b"Wuhan", "text/plain")},
            )
            assert upload.status_code == 201, upload.text
            artifact_id = upload.json()["input_artifact_id"]
            content = [
                TextInputContent(text=content),
                DocumentInputContent(
                    source=InputContentDataSource(value=artifact_id, mimeType="text/plain"),
                    metadata={"inputArtifactId": artifact_id},
                ),
            ]
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
                messages=[UserMessage(id="natural", content=content)],
                forwarded_props={"conversationSurface": "assistant"},
            ),
        )
        active_parent = parent.run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "fencing_token": parent.run.fencing_token + 1,
                "input": parent.run.input | {"model_route_override": "chosen-model"},
            }
        )
        assert await c.worker._runs.compare_and_set(parent.run.status, active_parent)
        session = await c.sessions.get("tenant-a", active_parent.session_id)
        overlay = await assistant_messaging_overlay(
            service, c.worker._events, active_parent, session
        )
        tools = {t.name: t.handler for t in overlay.tools}
        if creation:
            created = await tools["assistant_create_specialist"](
                {"request_key": "create", "name": bot.name, "description": bot.description}
            )
            assert not created.get("isError"), created
            assert not receipts  # A contact is not an execution or an implicit message.
        args = {"request_key": "send", "specialist_id": bot.specialist_id}
        await asyncio.gather(*(tools["assistant_send_message"](args) for _ in range(2)))
        recovered = await assistant_messaging_overlay(
            service, c.worker._events, active_parent, session
        )
        await next(t for t in recovered.tools if t.name == "assistant_send_message").handler(args)
        assert len(receipts) == 1
        receipt = next(iter(receipts.values()))
        child = await c.runs.get("tenant-a", receipt.run_id)
        assert child.session_id == before.session_id
        assert child.input["conversation_prompts"] == ["previous forecast", "武汉明天需要带伞吗"]
        assert child.input["assistant_dispatch"] is True
        assert child.input.get("input_artifact_ids", []) == ([artifact_id] if attached else [])
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
        # Answering from the main chat continues the same child conversation and
        # creates its own source-bound receipt; it never turns into a main Run.
        reply_headers = {**HEADERS, "Idempotency-Key": "answer-key-12345678"}
        reply_body = {
            "text": "只总结武汉这一项",
            "sourceThreadId": MAIN,
            "replyToRunId": child.run_id,
        }
        answer = await client.post(
            f"/v1/studio/assistant/specialists/{bot.specialist_id}/messages",
            headers=reply_headers,
            json=reply_body,
        )
        assert answer.status_code == 202, answer.text
        replay = await client.post(
            f"/v1/studio/assistant/specialists/{bot.specialist_id}/messages",
            headers=reply_headers,
            json=reply_body,
        )
        assert replay.json() == answer.json()
        assert len(receipts) == 2
        continued = await c.runs.get("tenant-a", answer.json()["runId"])
        assert continued.session_id == child.session_id
        assert continued.input["prompt"] == reply_body["text"]
        assert continued.input["model_route_override"] == "chosen-model"
        assert continued.input["conversation_prompts"][-1] == reply_body["text"]
        stale = await client.post(
            f"/v1/studio/assistant/specialists/{bot.specialist_id}/messages",
            headers={**HEADERS, "Idempotency-Key": "different-answer-12345678"},
            json=reply_body,
        )
        assert stale.status_code == 409
        foreign = await client.post(
            f"/v1/studio/assistant/specialists/{bot.specialist_id}/messages",
            headers={**HEADERS, "Idempotency-Key": "foreign-source-12345678"},
            json=reply_body | {"sourceThreadId": "assistant_unrelated"},
        )
        assert foreign.status_code in {404, 409}
        assert len(receipts) == 2
        forbidden = await client.get(
            f"/v1/agui/threads/{thread}/history", headers={**HEADERS, "X-User-ID": "other-user"}
        )
        assert forbidden.status_code == 404
