from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.config import Settings
from harness.quota.models import QuotaResource, QuotaScope, ReplaceQuotaPolicyRequest

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
IDENTITY_HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


@pytest.mark.asyncio
async def test_publish_create_run_query_and_cancel_are_tenant_scoped() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        published = await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=IDENTITY_HEADERS,
        )
        assert published.status_code == 201
        assert published.json()["name"] == "echo-agent"

        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=IDENTITY_HEADERS,
        )
        assert session.status_code == 201
        session_id = session.json()["session_id"]

        first = await client.post(
            f"/v1/sessions/{session_id}/runs",
            json={"prompt": "hello"},
            headers={**IDENTITY_HEADERS, "Idempotency-Key": "request-1"},
        )
        repeated = await client.post(
            f"/v1/sessions/{session_id}/runs",
            json={"prompt": "ignored on replay"},
            headers={**IDENTITY_HEADERS, "Idempotency-Key": "request-1"},
        )
        duplicate = await client.post(
            f"/v1/sessions/{session_id}/runs",
            json={"prompt": "hello"},
            headers={**IDENTITY_HEADERS, "Idempotency-Key": "request-2"},
        )
        assert first.status_code == 202
        assert repeated.status_code == 202
        assert repeated.json()["run_id"] == first.json()["run_id"]
        assert duplicate.json()["run_id"] == first.json()["run_id"]
        assert first.json()["input"] == {"prompt": "hello"}

        run_id = first.json()["run_id"]
        fetched = await client.get(f"/v1/runs/{run_id}", headers=IDENTITY_HEADERS)
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "queued"

        cancelled = await client.post(f"/v1/runs/{run_id}/cancel", headers=IDENTITY_HEADERS)
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_identity_headers_are_required_and_errors_are_structured() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)})

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "identity_required",
            "message": "Sign in with a valid access token",
        }
    }


@pytest.mark.asyncio
async def test_local_console_origin_is_allowed() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/v1/agents",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-tenant-id,x-user-id",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


@pytest.mark.asyncio
async def test_run_quota_rejection_is_stable_creates_no_half_run_and_cancel_releases() -> None:
    app = create_memory_app(settings=Settings(quota_enforcement_enabled=True))
    await app.state.container.quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner-a",
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            scope=QuotaScope(),
            limits={QuotaResource.CONCURRENT_RUNS: 1},
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (
            await client.post(
                "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=IDENTITY_HEADERS
            )
        ).status_code == 201
        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=IDENTITY_HEADERS,
        )
        path = f"/v1/sessions/{session.json()['session_id']}/runs"
        first = await client.post(
            path,
            json={"prompt": "first"},
            headers={**IDENTITY_HEADERS, "Idempotency-Key": "quota-1"},
        )
        rejected = await client.post(
            path,
            json={"prompt": "second"},
            headers={**IDENTITY_HEADERS, "Idempotency-Key": "quota-2"},
        )
        stored = await app.state.container.runs.list_for_sessions(
            "tenant-a", [session.json()["session_id"]]
        )
        cancelled = await client.post(
            f"/v1/runs/{first.json()['run_id']}/cancel", headers=IDENTITY_HEADERS
        )
        admitted = await client.post(
            path,
            json={"prompt": "third"},
            headers={**IDENTITY_HEADERS, "Idempotency-Key": "quota-3"},
        )

    assert first.status_code == 202
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "quota_exceeded"
    assert "concurrent_runs" in rejected.json()["error"]["message"]
    assert [run.run_id for run in stored] == [first.json()["run_id"]]
    assert cancelled.json()["status"] == "cancelled"
    assert admitted.status_code == 202
