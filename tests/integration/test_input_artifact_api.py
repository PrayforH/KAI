import hashlib

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_app, create_memory_app
from harness.api.dependencies import build_memory_container
from harness.core.errors import StorageCapacityError
from harness.core.models import ExecutionIdentity

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


class _FullArtifactStore:
    async def put(self, tenant_id: str, artifact_id: str, content: bytes):
        del tenant_id, artifact_id, content
        raise StorageCapacityError("attachment storage is full")


@pytest.mark.asyncio
async def test_upload_and_download_input_artifact_are_user_scoped() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        content = b"browser-selected bytes"
        uploaded = await client.post(
            "/v1/input-artifacts",
            files={"file": ("notes.txt", content, "text/plain")},
            headers=HEADERS,
        )

        assert uploaded.status_code == 201
        artifact = uploaded.json()
        assert artifact["input_artifact_id"].startswith("input_artifact_")
        assert artifact["name"] == "notes.txt"
        assert artifact["media_type"] == "text/plain"
        assert artifact["status"] == "ready"
        assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
        assert artifact["size_bytes"] == len(content)

        downloaded = await client.get(
            f"/v1/input-artifacts/{artifact['input_artifact_id']}/content",
            headers=HEADERS,
        )
        assert downloaded.status_code == 200
        assert downloaded.content == content

        cross_user = await client.get(
            f"/v1/input-artifacts/{artifact['input_artifact_id']}/content",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"},
        )
        assert cross_user.status_code == 404


@pytest.mark.asyncio
async def test_upload_rejects_oversized_input_without_returning_an_id() -> None:
    container = build_memory_container()
    container.input_artifacts.max_file_bytes = 4
    app = create_app(container)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/input-artifacts",
            files={"file": ("large.txt", b"12345", "text/plain")},
            headers=HEADERS,
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "input_artifact_too_large"
    assert "input_artifact_id" not in response.text


@pytest.mark.asyncio
async def test_upload_reports_storage_capacity_instead_of_generic_500() -> None:
    container = build_memory_container()
    container.input_artifacts._store = _FullArtifactStore()  # type: ignore[attr-defined]
    app = create_app(container)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/input-artifacts",
            files={"file": ("notes.txt", b"content", "text/plain")},
            headers=HEADERS,
        )

    assert response.status_code == 507
    assert response.json()["error"] == {
        "code": "storage_capacity_exceeded",
        "message": "attachment storage is full",
    }


@pytest.mark.asyncio
async def test_thread_file_catalog_api_is_user_scoped() -> None:
    container = build_memory_container()
    await container.agents.publish(
        "tenant-a", "user-1", "tests/fixtures/agents/echo-agent/agent.yaml"
    )
    session = await container.sessions.create("tenant-a", "user-1", "echo-agent", "0.1.0")
    identity = ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-1",
        project_id="echo-agent",
        session_id=session.session_id,
        run_id="run-a",
        agent_name="echo-agent",
        agent_version="0.1.0",
    )
    await container.file_catalog.record_original(
        identity=identity,
        input_artifact_id="input-a",
        name="notes.txt",
        media_type="text/plain",
        path="inputs/original/notes.txt",
    )
    app = create_app(container)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/threads/{session.session_id}/files", headers=HEADERS)
        cross_user = await client.get(
            f"/v1/threads/{session.session_id}/files",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"},
        )

    assert response.status_code == 200
    assert response.json()[0]["name"] == "notes.txt"
    assert cross_user.status_code == 404


@pytest.mark.asyncio
async def test_upload_limits_match_enforced_limit_and_require_permission() -> None:
    container = build_memory_container()
    container.input_artifacts.max_file_bytes = 1234
    app = create_app(container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/input-artifacts/limits", headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {
            "max_file_bytes": 1234, "max_files": 10, "max_total_bytes": 100 * 1024 * 1024
        }
        response = await client.get("/v1/input-artifacts/limits")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_three_large_pdf_attachments_fit_default_upload_and_run_limits() -> None:
    import asyncio

    container = build_memory_container()
    app = create_app(container)
    # Sizes of the reported three-file batch; the last exceeds the old 25 MiB cap.
    sizes = (21_539_928, 21_011_375, 26_603_594)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def upload(index: int, size: int):
            content = b"%PDF-1.7\n" + b" " * (size - 9)
            return await client.post(
                "/v1/input-artifacts",
                files={"file": (f"qa-{index}.pdf", content, "application/pdf")},
                headers=HEADERS,
            )

        responses = await asyncio.gather(*(upload(i, size) for i, size in enumerate(sizes)))
    assert [response.status_code for response in responses] == [201, 201, 201]
    assert [response.json()["size_bytes"] for response in responses] == list(sizes)
    assert container.input_artifacts.max_file_bytes == 50 * 1024 * 1024
    assert sum(sizes) < container.input_artifacts.max_total_bytes == 100 * 1024 * 1024
