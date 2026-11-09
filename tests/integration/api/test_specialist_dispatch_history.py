"""Generated specialist briefs stay in execution context, not duplicated user bubbles."""

import json

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from httpx import ASGITransport, AsyncClient

from harness.agui.routes import _history_messages_for_run
from harness.api.app import create_memory_app
from harness.api.dependencies import Identity
from tests.integration.api.test_agui_history_pagination import FIXTURE_MANIFEST, HEADERS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "server_dispatch,spoof_prop", [(True, False), (False, False), (False, True)]
)
async def test_only_internal_dispatch_replaces_brief_with_main_source(server_dispatch, spoof_prop):
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        c = app.state.container
        text = "本次任务：核对官网\n仅查这一次，保留完整执行要求"
        creation = await c.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            assistant_dispatch=server_dispatch,
            request=RunAgentInput(
                thread_id="assistant_objective_dispatch",
                run_id="dispatch-1",
                state={},
                messages=[UserMessage(id="input-1", content=text)],
                tools=[],
                context=[],
                forwarded_props={"assistant_dispatch": spoof_prop, "assistantDispatch": spoof_prop},
            ),
        )
        assert creation.run.input["prompt"] == text
        assert creation.run.input.get("assistant_dispatch", False) is server_dispatch
        await c.worker.execute("tenant-a", creation.run.run_id)
        endpoint = "/v1/agui/threads/assistant_objective_dispatch/history"
        body = (await client.get(endpoint, headers=HEADERS)).json()
        user_text = [m["content"] for m in body["messages"] if m["role"] == "user"]
        assert user_text == ([] if server_dispatch else [text])
        sources = [
            json.loads(call["function"]["arguments"])
            for message in body["messages"]
            for call in message.get("toolCalls") or []
            if call["function"]["name"] == "harness_notice_source"
        ]
        if server_dispatch:
            assert len(sources) == 1
            assert sources[0]["name"] == "主助理"
            assert sources[0]["href"] == "/assistant"
            assert sources[0]["direction"] == "received"
        else:
            assert sources == []
        assert body == (await client.get(endpoint, headers=HEADERS)).json()
        # A project Run cannot hide its actual user input using this Bot marker.
        project_run = creation.run.model_copy(
            update={
                "input": creation.run.input
                | {"assistant_dispatch": True, "business_project_id": "project-a"},
            }
        )
        projected = await _history_messages_for_run(
            c, Identity(tenant_id="tenant-a", user_id="user-1"), project_run, write_snapshot=False
        )
        assert [m.content for m in projected if m.role == "user"] == [text]


@pytest.mark.asyncio
async def test_bot_work_stays_in_history_without_consuming_task_page_slots():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        for thread_id, bot in (("ordinary-task", None), ("assistant_specialist_example", "bot")):
            await container.agui.create_run_with_result(
                tenant_id="tenant-a",
                user_id="user-1",
                agent_name="echo-agent",
                agent_version="0.1.0",
                bot_specialist_id=bot,
                request=RunAgentInput(
                    thread_id=thread_id,
                    run_id=thread_id,
                    state={},
                    messages=[UserMessage(id="request", content="核对原文")],
                    tools=[],
                    context=[],
                    forwarded_props={},
                ),
            )
        tasks = await client.get("/v1/agui/threads?limit=1", headers=HEADERS)
        assert tasks.status_code == 200
        assert [item["thread_id"] for item in tasks.json()] == ["ordinary-task"]
        history = await client.get(
            "/v1/agui/threads/assistant_specialist_example/history", headers=HEADERS
        )
        assert history.status_code == 200
        assert any(item["content"] == "核对原文" for item in history.json()["messages"])
