from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}
MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")


@pytest.mark.asyncio
async def test_create_empty_project_task_persists_without_running_and_can_run_later():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.post("/v1/agents", json={"path": str(MANIFEST)}, headers=HEADERS)
        ).status_code == 201
        project = (
            await client.post("/v1/studio/projects", json={"name": "New project"}, headers=HEADERS)
        ).json()
        body = {
            "project_id": project["projectId"],
            "agent_name": "echo-agent",
            "agent_version": "0.1.0",
        }
        created = await client.post("/v1/agui/threads", json=body, headers=HEADERS)
        assert created.status_code == 201, created.text
        task = created.json()
        assert task["project_id"] == project["projectId"]
        assert task["status"] == "idle"
        assert task["run_id"] is None
        tasks = (await client.get("/v1/agui/threads", headers=HEADERS)).json()
        assert [item["thread_id"] for item in tasks] == [task["thread_id"]]
        assert tasks[0]["project_id"] == project["projectId"]
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            headers=HEADERS,
            json={
                "threadId": task["thread_id"],
                "runId": "project-first-run",
                "state": {},
                "messages": [{"id": "message-1", "role": "user", "content": "hello"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )
        assert response.status_code == 200, response.text
        tasks = (await client.get("/v1/agui/threads", headers=HEADERS)).json()
        assert len(tasks) == 1
        assert tasks[0]["project_id"] == project["projectId"]
        assert tasks[0]["status"] == "succeeded"
        second = await client.post("/v1/agui/threads", json=body, headers=HEADERS)
        assert second.status_code == 201
        assert second.json()["thread_id"] != task["thread_id"]


@pytest.mark.asyncio
async def test_project_task_rejects_missing_foreign_archived_projects_and_foreign_agents():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(MANIFEST)}, headers=HEADERS)
        project = (
            await client.post("/v1/studio/projects", json={"name": "Private"}, headers=HEADERS)
        ).json()
        body = {
            "project_id": project["projectId"],
            "agent_name": "echo-agent",
            "agent_version": "0.1.0",
        }
        other = {**HEADERS, "X-User-ID": "user-2"}
        assert (await client.post("/v1/agui/threads", json=body, headers=other)).status_code == 404
        assert (
            await client.post(
                "/v1/agui/threads", json={**body, "project_id": "missing"}, headers=HEADERS
            )
        ).status_code == 404
        assert (
            await client.post(
                "/v1/agui/threads", json={**body, "agent_owner_user_id": "user-2"}, headers=HEADERS
            )
        ).status_code == 409
        await client.patch(
            f"/v1/studio/projects/{project['projectId']}", json={"archived": True}, headers=HEADERS
        )
        assert (
            await client.post("/v1/agui/threads", json=body, headers=HEADERS)
        ).status_code == 409
        assert (await client.get("/v1/agui/threads", headers=HEADERS)).json() == []
