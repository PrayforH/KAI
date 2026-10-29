from pathlib import Path
from typing import Any

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
        run_id = response.headers["x-harness-run-id"]
        context_url = f"/v1/runs/{run_id}/context"
        captured = (await client.get(context_url, headers=HEADERS)).json()
        assert captured["manifest"]["project"]["projectId"] == project["projectId"]
        assert captured["manifest"]["agent"]["name"] == "echo-agent"
        await client.patch(
            f"/v1/studio/projects/{project['projectId']}",
            json={"name": "Edited later"},
            headers=HEADERS,
        )
        assert (await client.get(context_url, headers=HEADERS)).json() == captured
        assert (
            await client.get(context_url, headers={**HEADERS, "X-User-ID": "other"})
        ).status_code == 404
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


@pytest.mark.asyncio
async def test_project_tasks_filter_before_limit_and_keep_owner_scope():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(MANIFEST)}, headers=HEADERS)
        projects: list[dict[str, Any]] = []
        for name in ("Quarterly review", "Unrelated work"):
            projects.append(
                (
                    await client.post("/v1/studio/projects", json={"name": name}, headers=HEADERS)
                ).json()
            )
        expected: list[str] = []
        for index in range(58):
            project = projects[0 if index < 3 else 1]
            task = await client.post(
                "/v1/agui/threads",
                headers=HEADERS,
                json={
                    "project_id": project["projectId"],
                    "agent_name": "echo-agent",
                    "agent_version": "0.1.0",
                },
            )
            assert task.status_code == 201, task.text
            if index < 3:
                expected.append(task.json()["thread_id"])
        path = f"/v1/agui/threads?project_id={projects[0]['projectId']}&limit=2"
        page1 = (await client.get(path, headers=HEADERS)).json()
        page2 = (await client.get(path + "&offset=2", headers=HEADERS)).json()
        assert len(page1) == 2 and len(page2) == 1
        assert {task["thread_id"] for task in page1 + page2} == set(expected)
        assert {task["project_id"] for task in page1 + page2} == {projects[0]["projectId"]}
        direct = await client.get(f"/v1/agui/threads?thread_id={expected[0]}", headers=HEADERS)
        assert direct.json()[0]["thread_id"] == expected[0]
        foreign = {**HEADERS, "X-User-ID": "user-2"}
        assert (await client.get(path, headers=foreign)).status_code == 404
        assert (
            await client.get(f"/v1/agui/threads?thread_id={expected[0]}", headers=foreign)
        ).status_code == 404


@pytest.mark.asyncio
async def test_project_results_include_archived_sources_without_crossing_projects_or_owners():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(MANIFEST)}, headers=HEADERS)
        projects = [
            (await client.post("/v1/studio/projects", json={"name": name}, headers=HEADERS)).json()
            for name in ("Current project", "Other project")
        ]
        expected: list[str] = []
        for index, project in enumerate((projects[0], projects[0], projects[1])):
            task = (
                await client.post(
                    "/v1/agui/threads",
                    headers=HEADERS,
                    json={
                        "project_id": project["projectId"],
                        "agent_name": "echo-agent",
                        "agent_version": "0.1.0",
                    },
                )
            ).json()
            thread_id = task["thread_id"]
            response = await client.post(
                "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
                headers=HEADERS,
                json={
                    "threadId": thread_id,
                    "runId": f"result-{index}",
                    "state": {},
                    "messages": [{"id": f"message-{index}", "role": "user", "content": "hello"}],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
            )
            run_id = response.headers["x-harness-run-id"]
            artifact = (
                await client.post(
                    f"/v1/runs/{run_id}/artifacts",
                    headers=HEADERS,
                    files={"file": (f"result-{index}.txt", b"result", "text/plain")},
                )
            ).json()
            if index < 2:
                expected.append(artifact["artifact_id"])
            if index == 1:
                await client.patch(
                    f"/v1/agui/threads/{thread_id}", json={"archived": True}, headers=HEADERS
                )
                direct = await client.get(
                    f"/v1/agui/threads?thread_id={thread_id}", headers=HEADERS
                )
                assert direct.json()[0]["archived_at"] is not None
        path = f"/v1/artifacts?project_id={projects[0]['projectId']}"
        results = await client.get(path, headers=HEADERS)
        assert results.status_code == 200
        assert {item["artifact_id"] for item in results.json()} == set(expected)
        assert sum(item["task_archived"] for item in results.json()) == 1
        assert (
            await client.get(path, headers={**HEADERS, "X-User-ID": "user-2"})
        ).status_code == 404
