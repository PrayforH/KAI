import shutil
from pathlib import Path
from typing import Any

import pytest
from ag_ui.core import RunAgentInput
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}
FIXTURE = Path("tests/fixtures/agents/echo-agent/agent.yaml")


@pytest.mark.asyncio
async def test_version_selection_is_durable_without_a_new_run(tmp_path: Path) -> None:
    app = create_memory_app(auto_execute=True)
    target = tmp_path / "echo-agent"
    shutil.copytree(FIXTURE.parent, target)
    manifest = target / "agent.yaml"
    manifest.write_text(manifest.read_text().replace("version: 0.1.0", "version: 0.2.0"))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS
    ) as client:
        for path in [FIXTURE, manifest]:
            assert (await client.post("/v1/agents", json={"path": str(path)})).status_code == 201
        request: dict[str, Any] = {
            "threadId": "version-task",
            "runId": "old-run",
            "state": {},
            "messages": [{"id": "msg", "role": "user", "content": "keep my history"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
        assert (
            await client.post("/v1/agui?agent_name=echo-agent&agent_version=0.1.0", json=request)
        ).status_code == 200
        before = (await client.get("/v1/agui/threads/version-task/history")).json()
        endpoint = "/v1/agui/threads/version-task/version"
        # Version existence and ownership remain server-authoritative.
        assert (await client.put(endpoint, json={"agent_version": "9.9.9"})).status_code == 404
        assert (
            await client.put(
                endpoint,
                json={"agent_version": "0.2.0"},
                headers={**HEADERS, "X-User-ID": "user-2"},
            )
        ).status_code == 404
        assert (
            await client.put("/v1/agui/threads/missing/version", json={"agent_version": "0.2.0"})
        ).status_code == 404
        for _ in range(2):
            changed = await client.put(endpoint, json={"agent_version": "0.2.0"})
            assert changed.status_code == 200, changed.text
        listed = (await client.get("/v1/agui/threads")).json()
        assert listed[0]["agent_version"] == "0.2.0"
        after = (await client.get("/v1/agui/threads/version-task/history")).json()
        assert after["messages"] == before["messages"]
        binding = await app.state.container.agui.get_binding(
            tenant_id="tenant-a", user_id="user-1", thread_id="version-task"
        )
        assert len(binding.previous_session_ids) == 1
        runs = await app.state.container.runs.list_for_sessions(
            "tenant-a", list(binding.session_ids), limit=200
        )
        assert len(runs) == 1
        # An actual active run must still lock the selector's server operation.
        active = await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.2.0",
            request=RunAgentInput.model_validate({**request, "runId": "active-run"}),
        )
        assert (await client.put(endpoint, json={"agent_version": "0.1.0"})).status_code == 409
        assert (await client.get("/v1/agui/threads")).json()[0]["agent_version"] == "0.2.0"
        await app.state.container.runs.cancel("tenant-a", active.run_id)
        assert (await client.put(endpoint, json={"agent_version": "0.1.0"})).status_code == 200
