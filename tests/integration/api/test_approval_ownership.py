from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_app
from harness.api.dependencies import build_memory_container
from harness.core.models import ApprovalStatus, RunStatus
from tests.support.policies import fake_runtime_review_profiles

FIXTURE = Path("tests/fixtures/agents/echo-agent/agent.yaml")


@pytest.mark.asyncio
async def test_only_the_run_owner_can_decide_an_approval() -> None:
    container = build_memory_container(policy_profiles=fake_runtime_review_profiles())
    await container.agents.publish("tenant-a", "owner", FIXTURE)
    session = await container.sessions.create("tenant-a", "owner", "echo-agent", "0.1.0")
    run = await container.runs.create(
        "tenant-a",
        session.session_id,
        "approval-owner-test",
        input={"prompt": "[approval] write a result"},
    )
    waiting = await container.worker.execute("tenant-a", run.run_id)
    assert waiting.status is RunStatus.WAITING_APPROVAL
    events = await container.events.list_after("tenant-a", run.run_id, 0)
    approval_event = next(event for event in events if event.type == "approval.requested")
    approval_id = str(approval_event.payload["approval_id"])

    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as client:
        denied = await client.put(
            f"/v1/approvals/{approval_id}",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "other-user"},
            json={"decision": "approved"},
        )
        approval = await container.approvals.get("tenant-a", approval_id)
        allowed = await client.put(
            f"/v1/approvals/{approval_id}",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "owner"},
            json={"decision": "approved"},
        )

    assert denied.status_code == 404
    assert approval.status is ApprovalStatus.PENDING
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "approved"
