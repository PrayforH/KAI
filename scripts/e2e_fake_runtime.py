"""Deterministic phase-one E2E verifier with no model key or Langfuse."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.core.models import RunStatus
from harness.policy.models import PolicyDecision, PolicyRule
from harness.policy.profiles import PolicyProfileRegistry, read_only_policy_rules
from harness.policy.rules import PolicyEngine, default_policy_rules

HEADERS = {"X-Tenant-ID": "local", "X-User-ID": "e2e"}
MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")


def _fake_runtime_review_profiles() -> PolicyProfileRegistry:
    review = PolicyEngine(
        [
            *default_policy_rules(),
            PolicyRule(
                name="fake-runtime-reviewed-command",
                tool="Bash",
                command_contains="printf 'reviewed operation'",
                decision=PolicyDecision.ASK,
                priority=1_000,
            ),
        ]
    )
    return PolicyProfileRegistry(
        {
            "production-read-only": PolicyEngine(read_only_policy_rules()),
            "production-standard": review,
            "production-orchestrator": review,
            "local-standard": review,
        }
    )


async def run_fake_e2e() -> dict[str, Any]:
    app = create_memory_app(policy_profiles=_fake_runtime_review_profiles())
    container = app.state.container
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        published = await client.post("/v1/agents", json={"path": str(MANIFEST)}, headers=HEADERS)
        published.raise_for_status()
        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=HEADERS,
        )
        session.raise_for_status()
        run = await client.post(
            f"/v1/sessions/{session.json()['session_id']}/runs",
            json={"prompt": "[approval] [artifact] validate phase one"},
            headers={**HEADERS, "Idempotency-Key": "phase-one-e2e"},
        )
        run.raise_for_status()
        run_id = run.json()["run_id"]

        paused = await container.worker.execute("local", run_id)
        if paused.status is not RunStatus.WAITING_APPROVAL:
            raise AssertionError(f"expected approval pause, got {paused.status}")
        events = await container.events.list_after("local", run_id, 0)
        approval_event = next(event for event in events if event.type == "approval.requested")
        approval_id = str(approval_event.payload["approval_id"])
        approved = await client.put(
            f"/v1/approvals/{approval_id}",
            json={"decision": "approved"},
            headers=HEADERS,
        )
        approved.raise_for_status()

        completed = await container.worker.execute("local", run_id)
        if completed.status is not RunStatus.SUCCEEDED:
            raise AssertionError(f"expected success, got {completed.status}")
        artifact_response = await client.get(f"/v1/runs/{run_id}/artifacts", headers=HEADERS)
        artifact_response.raise_for_status()
        artifacts = artifact_response.json()
        if len(artifacts) != 1:
            raise AssertionError(f"expected one artifact, got {len(artifacts)}")
        artifact = artifacts[0]
        downloaded = await client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content", headers=HEADERS
        )
        downloaded.raise_for_status()
        digest = hashlib.sha256(downloaded.content).hexdigest()
        if digest != artifact["sha256"]:
            raise AssertionError("downloaded artifact hash mismatch")
        agui = await client.get(f"/v1/agui/runs/{run_id}/events", headers=HEADERS)
        agui.raise_for_status()

    if container.observability.enabled or container.observability.exporter is not None:
        raise AssertionError("local E2E unexpectedly enabled observability export")
    return {
        "run_id": run_id,
        "status": completed.status.value,
        "artifact_sha256": digest,
        "approval_id": approval_id,
        "agui_events": agui.text.count("data:"),
        "otel_enabled": container.observability.enabled,
    }


async def main() -> None:
    report = await run_fake_e2e()
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
