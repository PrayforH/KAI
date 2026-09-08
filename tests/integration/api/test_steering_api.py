from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


@pytest.mark.asyncio
async def test_steer_requires_owner_and_active_runtime_and_valid_input():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            headers=HEADERS,
            json={"path": str(Path("tests/fixtures/agents/echo-agent/agent.yaml"))},
        )
        session = await client.post(
            "/v1/sessions",
            headers=HEADERS,
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
        )
        run = await client.post(
            f"/v1/sessions/{session.json()['session_id']}/runs",
            headers={**HEADERS, "Idempotency-Key": "initial"},
            json={"prompt": "hello"},
        )
        url = f"/v1/runs/{run.json()['run_id']}/steer"
        body = {"request_id": "one", "text": "guide"}
        assert (await client.post(url, json=body)).status_code == 401
        assert (
            await client.post(url, json=body, headers={**HEADERS, "X-User-ID": "other"})
        ).status_code == 404
        assert (
            await client.get(url, headers={**HEADERS, "X-Tenant-ID": "other"})
        ).status_code == 404
        assert (
            await client.post(url, headers=HEADERS, json={**body, "text": ""})
        ).status_code == 422
        assert (await client.post(url, headers=HEADERS, json=body)).status_code == 409
        state = await client.get(url, headers=HEADERS)
        assert state.json() == {"available": False, "requests": []}
