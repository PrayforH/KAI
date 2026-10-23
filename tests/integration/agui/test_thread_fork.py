"""A branch freezes visible history and starts with an independent SDK identity."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


@pytest.mark.asyncio
async def test_branch_freezes_history_and_preserves_project_and_execution_context():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS
    ) as client:
        await client.post(
            "/v1/agents", json={"path": str(Path("tests/fixtures/agents/echo-agent/agent.yaml"))}
        )
        project = (await client.post("/v1/studio/projects", json={"name": "Branch project"})).json()
        source = (
            await client.post(
                "/v1/agui/threads",
                json={
                    "project_id": project["projectId"],
                    "agent_name": "echo-agent",
                    "agent_version": "0.1.0",
                },
            )
        ).json()

        async def send(thread_id: str, run_id: str, prompt: str) -> str:
            response = await client.post(
                "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
                json={
                    "threadId": thread_id,
                    "runId": run_id,
                    "state": {},
                    "messages": [
                        {"id": run_id, "role": "user", "content": prompt},
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )
            assert response.status_code == 200, response.text
            return response.headers["x-harness-run-id"]

        await send(source["thread_id"], "source-first", "Remember branch token: orchid-731")
        before = (await client.get(f"/v1/agui/threads/{source['thread_id']}/history")).json()
        response = await client.post(f"/v1/agui/threads/{source['thread_id']}/fork")
        assert response.status_code == 201, response.text
        branch = response.json()
        assert branch["thread_id"] != source["thread_id"]
        assert branch["session_id"] != source["session_id"]
        assert branch["project_id"] == project["projectId"]
        assert branch["status"] == "idle"
        history = (await client.get(f"/v1/agui/threads/{branch['thread_id']}/history")).json()
        assert history["messages"] == before["messages"]
        session = await app.state.container.sessions.get("tenant-a", branch["session_id"])
        assert session.resolved_runtime_thread_id is None

        await send(source["thread_id"], "source-later", "This message belongs only to the original")
        frozen = (await client.get(f"/v1/agui/threads/{branch['thread_id']}/history")).json()
        assert frozen["messages"] == before["messages"]
        run_id = await send(branch["thread_id"], "branch-first", "Continue from the branch point")
        run = await app.state.container.runs.get("tenant-a", run_id)
        assert "orchid-731" in run.input["conversation_branch_context"]
        assert "belongs only to the original" not in run.input["conversation_branch_context"]
        source_after = (await client.get(f"/v1/agui/threads/{source['thread_id']}/history")).json()
        assert "Continue from the branch point" not in str(source_after["messages"])
        branch_after = (await client.get(f"/v1/agui/threads/{branch['thread_id']}/history")).json()
        assert branch_after["messages"][: len(before["messages"])] == before["messages"]
        grandchild = (await client.post(f"/v1/agui/threads/{branch['thread_id']}/fork")).json()
        assert (await client.get(f"/v1/agui/threads/{grandchild['thread_id']}/history")).json()[
            "messages"
        ] == branch_after["messages"]
        stranger = {**HEADERS, "X-User-ID": "intruder"}
        assert (
            await client.post(f"/v1/agui/threads/{source['thread_id']}/fork", headers=stranger)
        ).status_code == 404
        assert (
            await client.get(f"/v1/agui/threads/{branch['thread_id']}/history", headers=stranger)
        ).status_code == 404


@pytest.mark.asyncio
async def test_empty_chat_can_branch_but_active_run_cannot():
    from ag_ui.core import RunAgentInput, UserMessage

    app = create_memory_app(auto_execute=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS
    ) as client:
        await client.post(
            "/v1/agents", json={"path": str(Path("tests/fixtures/agents/echo-agent/agent.yaml"))}
        )
        project = (await client.post("/v1/studio/projects", json={"name": "Empty branch"})).json()
        source = (
            await client.post(
                "/v1/agui/threads",
                json={
                    "project_id": project["projectId"],
                    "agent_name": "echo-agent",
                    "agent_version": "0.1.0",
                },
            )
        ).json()
        assert (
            await client.post(f"/v1/agui/threads/{source['thread_id']}/fork")
        ).status_code == 201
        await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput(
                thread_id=source["thread_id"],
                run_id="active",
                state={},
                tools=[],
                context=[],
                forwarded_props={},
                messages=[UserMessage(id="user", content="still running")],
            ),
        )
        assert (
            await client.post(f"/v1/agui/threads/{source['thread_id']}/fork")
        ).status_code == 409
