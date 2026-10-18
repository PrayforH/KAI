from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


@pytest.mark.asyncio
async def test_saved_results_are_explicit_versioned_owned_snapshots():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(Path("tests/fixtures/agents/echo-agent/agent.yaml"))},
            headers=HEADERS,
        )
        project = (
            await client.post("/v1/studio/projects", json={"name": "Deliverables"}, headers=HEADERS)
        ).json()
        project_id = project["projectId"]
        task = (
            await client.post(
                "/v1/agui/threads",
                json={
                    "project_id": project_id,
                    "agent_name": "echo-agent",
                    "agent_version": "0.1.0",
                },
                headers=HEADERS,
            )
        ).json()
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            headers=HEADERS,
            json={
                "threadId": task["thread_id"],
                "runId": "result-run",
                "state": {},
                "messages": [{"id": "m1", "role": "user", "content": "hello"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.headers["x-harness-run-id"]
        url = f"/v1/studio/projects/{project_id}/results"
        assert (await client.get(url, headers=HEADERS)).json() == []
        saved = []
        for content in (b"first version", b"second version"):
            output = (
                await client.post(
                    f"/v1/runs/{run_id}/artifacts",
                    headers=HEADERS,
                    files={"file": ("report.txt", content, "text/plain")},
                )
            ).json()
            response = await client.post(
                url, headers=HEADERS, json={"artifactId": output["artifact_id"]}
            )
            assert response.status_code == 201, response.text
            result = response.json()
            assert result["threadId"] == task["thread_id"]
            assert result["sha256"]
            snapshot = await app.state.container.input_artifacts.download(
                tenant_id="tenant-a", user_id="user-1", input_artifact_id=result["inputArtifactId"]
            )
            assert snapshot[1] == content
            saved.append(result)
            assert (
                await client.post(url, headers=HEADERS, json={"artifactId": output["artifact_id"]})
            ).json() == result
        assert [r["version"] for r in saved] == [1, 2]
        finalized = await client.patch(
            f"{url}/{saved[0]['resultId']}", headers=HEADERS, json={"finalized": True}
        )
        assert finalized.json()["finalizedAt"]
        other = {**HEADERS, "X-User-ID": "intruder"}
        assert (await client.get(url, headers=other)).status_code == 404
        assert (
            await client.patch(
                f"{url}/{saved[0]['resultId']}", headers=other, json={"finalized": False}
            )
        ).status_code == 404
        unrelated = (
            await client.post("/v1/studio/projects", headers=HEADERS, json={"name": "Other"})
        ).json()
        assert (
            await client.post(
                f"/v1/studio/projects/{unrelated['projectId']}/results",
                headers=HEADERS,
                json={"artifactId": saved[0]["artifactId"]},
            )
        ).status_code == 404
        # Moving the task later cannot remove saved results from their original project.
        moved = await client.patch(
            f"/v1/agui/threads/{task['thread_id']}",
            headers=HEADERS,
            json={"project_id": None},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["project_id"] is None
        retained = (await client.get(url, headers=HEADERS)).json()
        assert len(retained) == 2
        assert retained[-1]["inputArtifactId"] == saved[0]["inputArtifactId"]
        resources = (
            await client.get(f"/v1/studio/projects/{project_id}/resources", headers=HEADERS)
        ).json()
        assert resources["documents"] == []
