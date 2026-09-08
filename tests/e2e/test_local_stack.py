from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.api.dependencies import build_memory_container
from harness.core.models import ApprovalStatus, RunStatus
from scripts.bootstrap_local_agent import (
    DEFAULT_MANIFEST,
    bootstrap_local_agents,
    local_client_options,
)
from scripts.e2e_fake_runtime import run_fake_e2e
from tests.support.policies import fake_runtime_review_profiles


@pytest.mark.asyncio
async def test_local_fake_runtime_stack() -> None:
    report = await run_fake_e2e()

    assert report["status"] == "succeeded"
    assert report["otel_enabled"] is False
    assert report["agui_events"] >= 10


@pytest.mark.asyncio
async def test_local_bootstrap_publishes_default_agent_for_agui() -> None:
    assert DEFAULT_MANIFEST == Path("agents/echo-agent/agent.yaml")
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await bootstrap_local_agents(client)
        await bootstrap_local_agents(client)
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.4.1",
            headers={"X-Tenant-ID": "local", "X-User-ID": "developer"},
            json={
                "threadId": "bootstrap-thread",
                "runId": "bootstrap-run",
                "state": {},
                "messages": [{"id": "message-1", "role": "user", "content": "hello"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    assert response.status_code == 200
    assert '"type":"RUN_FINISHED"' in response.text


@pytest.mark.asyncio
async def test_local_bootstrap_publishes_referenced_helper_agent() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await bootstrap_local_agents(client)
        response = await client.post(
            "/v1/sessions",
            headers={"X-Tenant-ID": "local", "X-User-ID": "developer"},
            json={"agent_name": "helper-agent", "agent_version": "1.0.0"},
        )

    assert response.status_code == 201


def test_local_bootstrap_does_not_route_loopback_through_system_proxy() -> None:
    assert local_client_options("http://127.0.0.1:8000") == {
        "base_url": "http://127.0.0.1:8000",
        "timeout": 10,
        "trust_env": False,
    }


def test_dev_up_routes_web_to_published_validation_agent_version() -> None:
    script = Path("scripts/dev_up.sh").read_text()

    assert "HARNESS_AGENT_VERSION=0.4.1" in script
    assert "Local Agent: echo-agent@0.4.1" in script


@pytest.mark.parametrize("script_name", ["dev_up.sh", "dev_down.sh"])
def test_local_lifecycle_supplies_non_production_compose_defaults(
    script_name: str,
) -> None:
    script = Path("scripts", script_name).read_text()

    for assignment in (
        'POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-harness}"',
        'MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"',
        'MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"',
        'HARNESS_NEW_API_BASE_URL="${HARNESS_NEW_API_BASE_URL:-http://127.0.0.1:1}"',
        'HARNESS_NEW_API_KEY="${HARNESS_NEW_API_KEY:-local-placeholder}"',
        'HARNESS_NEW_API_MODEL="${HARNESS_NEW_API_MODEL:-local-placeholder}"',
    ):
        assert assignment in script


def test_dev_up_starts_only_local_infrastructure_from_production_compose() -> None:
    script = Path("scripts/dev_up.sh").read_text()

    assert "up -d postgres redis minio minio-init" in script


def test_domain_agent_guide_documents_shared_general_capabilities() -> None:
    guide = Path("docs/domain-agents.md").read_text()

    assert "mcp: tavily-readonly" in guide
    assert "helper-agent@1.0.0" in guide
    assert "共享审批与运行界面" in guide
    assert "Policy" in guide


def test_local_lifecycle_tracks_the_real_server_processes() -> None:
    start = Path("scripts/dev_up.sh").read_text()
    stop = Path("scripts/dev_down.sh").read_text()

    assert "nohup uv run uvicorn harness.api.app:app" in start
    assert "nohup npm run dev -- --hostname 127.0.0.1" in start
    assert 'stop_matching "$ROOT/.venv/bin/uvicorn harness.api.app:app"' in stop
    assert 'stop_matching "$ROOT/web/harness-console/node_modules/.bin/next dev"' in stop


@pytest.mark.asyncio
async def test_approval_resume_closes_message_and_uses_a_new_message_id() -> None:
    container = build_memory_container(policy_profiles=fake_runtime_review_profiles())
    await container.agents.publish(
        "local", "developer", "tests/fixtures/agents/echo-agent/agent.yaml"
    )
    session = await container.sessions.create("local", "developer", "echo-agent", "0.1.0")
    run = await container.runs.create(
        "local",
        session.session_id,
        "approval-message-lifecycle",
        input={"prompt": "[approval] [artifact] verify"},
    )

    paused = await container.worker.execute("local", run.run_id)
    assert paused.status is RunStatus.WAITING_APPROVAL
    paused_events = await container.events.list_after("local", run.run_id, 0)
    assert paused_events[-1].type == "message.completed"

    approval_event = next(item for item in paused_events if item.type == "approval.requested")
    await container.approvals.decide(
        tenant_id="local",
        approval_id=str(approval_event.payload["approval_id"]),
        decision=ApprovalStatus.APPROVED,
    )
    completed = await container.worker.execute("local", run.run_id)
    assert completed.status is RunStatus.SUCCEEDED

    all_events = await container.events.list_after("local", run.run_id, 0)
    message_ids = [
        str(item.payload["message_id"]) for item in all_events if item.type == "message.start"
    ]
    assert len(message_ids) == 2
    assert len(set(message_ids)) == 2
