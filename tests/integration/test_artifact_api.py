import hashlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.config import Settings
from harness.quota.models import QuotaResource, ReplaceQuotaPolicyRequest

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


@pytest.mark.asyncio
async def test_upload_list_and_download_artifact_are_tenant_scoped() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=HEADERS,
        )
        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=HEADERS,
        )
        run = await client.post(
            f"/v1/sessions/{session.json()['session_id']}/runs",
            json={"prompt": "hello"},
            headers={**HEADERS, "Idempotency-Key": "artifact-run"},
        )
        run_id = run.json()["run_id"]
        cross_user_headers = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"}
        cross_user_run = await client.get(f"/v1/runs/{run_id}", headers=cross_user_headers)
        assert cross_user_run.status_code == 404
        cross_user_create = await client.post(
            f"/v1/sessions/{session.json()['session_id']}/runs",
            json={"prompt": "not mine"},
            headers={**cross_user_headers, "Idempotency-Key": "cross-user"},
        )
        assert cross_user_create.status_code == 404
        content = b"artifact bytes"
        uploaded = await client.post(
            f"/v1/runs/{run_id}/artifacts",
            files={"file": ("result.txt", content, "text/plain")},
            headers=HEADERS,
        )
        assert uploaded.status_code == 201
        artifact = uploaded.json()
        assert artifact["status"] == "ready"
        assert artifact["sha256"] == hashlib.sha256(content).hexdigest()

        listed = await client.get(f"/v1/runs/{run_id}/artifacts", headers=HEADERS)
        assert [item["artifact_id"] for item in listed.json()] == [artifact["artifact_id"]]
        downloaded = await client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content", headers=HEADERS
        )
        assert downloaded.content == content

        for method, url in (
            ("GET", f"/v1/runs/{run_id}/artifacts"),
            ("GET", f"/v1/artifacts/{artifact['artifact_id']}/content"),
            ("POST", f"/v1/runs/{run_id}/cancel"),
            ("GET", f"/v1/runs/{run_id}/events"),
            ("GET", f"/v1/agui/runs/{run_id}/events"),
        ):
            response = await client.request(method, url, headers=cross_user_headers)
            assert response.status_code == 404

        cross_tenant = await client.get(
            f"/v1/artifacts/{artifact['artifact_id']}/content",
            headers={"X-Tenant-ID": "tenant-b", "X-User-ID": "user-2"},
        )
        assert cross_tenant.status_code == 404


@pytest.mark.asyncio
async def test_my_files_indexes_ready_artifacts_across_active_and_archived_tasks() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=HEADERS,
        )
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json={
                "threadId": "thread-deliverables",
                "runId": "client-deliverables",
                "state": {},
                "messages": [
                    {"id": "message-deliverables", "role": "user", "content": "生成季度报告"}
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
            headers=HEADERS,
        )
        run_id = response.headers["x-harness-run-id"]
        uploaded = await client.post(
            f"/v1/runs/{run_id}/artifacts",
            files={
                "file": (
                    "季度报告.docx",
                    b"document",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=HEADERS,
        )
        active = await client.get("/v1/artifacts", headers=HEADERS)
        await client.patch(
            "/v1/agui/threads/thread-deliverables",
            json={"archived": True},
            headers=HEADERS,
        )
        archived = await client.get("/v1/artifacts", headers=HEADERS)
        other_user = await client.get(
            "/v1/artifacts",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"},
        )

    assert uploaded.status_code == 201
    assert active.status_code == 200
    assert active.json() == [
        {
            "artifact_id": uploaded.json()["artifact_id"],
            "name": "季度报告.docx",
            "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": 8,
            "run_id": run_id,
            "thread_id": "thread-deliverables",
            "thread_title": "生成季度报告",
            "agent_name": "echo-agent",
            "created_at": active.json()[0]["created_at"],
            "task_archived": False,
        }
    ]
    assert archived.json()[0]["task_archived"] is True
    assert other_user.json() == []


@pytest.mark.asyncio
async def test_artifact_upload_stops_at_configured_size_limit() -> None:
    app = create_memory_app()
    app.state.container.artifacts.max_file_bytes = 4
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=HEADERS,
        )
        run = await client.post(
            f"/v1/sessions/{session.json()['session_id']}/runs",
            json={"prompt": "hello"},
            headers={**HEADERS, "Idempotency-Key": "artifact-limit"},
        )
        response = await client.post(
            f"/v1/runs/{run.json()['run_id']}/artifacts",
            files={"file": ("large.txt", b"12345", "text/plain")},
            headers=HEADERS,
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "artifact_too_large"


@pytest.mark.asyncio
async def test_artifact_quota_is_checked_before_pending_metadata_is_created() -> None:
    app = create_memory_app(settings=Settings(quota_enforcement_enabled=True))
    await app.state.container.quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner-a",
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            limits={QuotaResource.ARTIFACT_BYTES: 4},
        ),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=HEADERS,
        )
        run = await client.post(
            f"/v1/sessions/{session.json()['session_id']}/runs",
            json={"prompt": "hello"},
            headers={**HEADERS, "Idempotency-Key": "artifact-quota"},
        )
        run_id = run.json()["run_id"]
        rejected = await client.post(
            f"/v1/runs/{run_id}/artifacts",
            files={"file": ("too-large.txt", b"12345", "text/plain")},
            headers=HEADERS,
        )
        listed = await client.get(f"/v1/runs/{run_id}/artifacts", headers=HEADERS)

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "quota_exceeded"
    assert "artifact_bytes" in rejected.json()["error"]["message"]
    assert listed.json() == []


@pytest.mark.asyncio
async def test_task_files_are_isolated_for_two_tasks_of_the_same_agent() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        artifacts = {}
        for thread_id in ("task-a", "task-b"):
            response = await client.post(
                "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
                json={
                    "threadId": thread_id,
                    "runId": f"run-{thread_id}",
                    "state": {},
                    "messages": [
                        {"id": f"message-{thread_id}", "role": "user", "content": thread_id}
                    ],
                    "tools": [],
                    "context": [],
                    "forwardedProps": {},
                },
                headers=HEADERS,
            )
            assert response.status_code == 200
            run_id = response.headers["x-harness-run-id"]
            uploaded = await client.post(
                f"/v1/runs/{run_id}/artifacts",
                headers=HEADERS,
                files={"file": ("result.txt", thread_id.encode(), "text/plain")},
            )
            assert uploaded.status_code == 201
            artifacts[thread_id] = uploaded.json()["artifact_id"]
        for current, other in (("task-a", "task-b"), ("task-b", "task-a")):
            listed = await client.get(
                "/v1/artifacts", params={"thread_id": current, "limit": 1}, headers=HEADERS
            )
            assert listed.status_code == 200
            assert [item["artifact_id"] for item in listed.json()] == [artifacts[current]]
            own = await client.get(
                f"/v1/artifacts/{artifacts[current]}/content",
                params={"thread_id": current},
                headers=HEADERS,
            )
            assert own.content == current.encode()
            foreign = await client.get(
                f"/v1/artifacts/{artifacts[other]}/content",
                params={"thread_id": current},
                headers=HEADERS,
            )
            assert foreign.status_code == 404
        unknown = await client.get("/v1/artifacts?thread_id=missing", headers=HEADERS)
        assert unknown.status_code == 404
        denied = await client.get(
            "/v1/artifacts?thread_id=task-a", headers={**HEADERS, "X-User-ID": "someone-else"}
        )
        assert denied.status_code == 404
        global_files = await client.get("/v1/artifacts", headers=HEADERS)
        assert len(global_files.json()) == 2
