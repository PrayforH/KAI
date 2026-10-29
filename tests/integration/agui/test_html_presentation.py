"""HTML delivery rechecks ownership and the exact published bytes on every read."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_app
from harness.api.dependencies import ApiContainer, build_memory_container
from harness.core.models import Artifact
from harness.presentation.schema import Presentation
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.runtime.fake import FakeRuntime
from tests.support.policies import fake_runtime_review_profiles

H = {"X-Tenant-ID": "tenant-a", "X-User-ID": "owner"}
CONTENT = b"<!doctype html><html><body><button>fixture</button></body></html>"


@pytest_asyncio.fixture
async def html_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[ApiContainer, Presentation]:
    async def execute(self: FakeRuntime, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        path = context.workspace / "outputs" / "tool.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(CONTENT)
        assert context.artifact_publisher is not None
        artifact = await context.artifact_publisher.publish(path="outputs/tool.html")
        block = {
            "version": 1,
            "kind": "html",
            "fallback_text": "An interactive fixture",
            "data": {"artifact_id": artifact.artifact_id},
        }
        yield RuntimeEvent(type="message.start", payload={"role": "assistant"})
        yield RuntimeEvent(
            type="message.delta", payload={"text": "```harness-ui\n" + json.dumps(block) + "\n```"}
        )
        yield RuntimeEvent(type="message.completed", payload={"role": "assistant"})

    monkeypatch.setattr(FakeRuntime, "execute", execute)
    container = build_memory_container(policy_profiles=fake_runtime_review_profiles())
    await container.agents.publish(
        "tenant-a", "owner", Path("tests/fixtures/agents/echo-agent/agent.yaml")
    )
    session = await container.sessions.create("tenant-a", "owner", "echo-agent", "0.1.0")
    run = await container.runs.create(
        "tenant-a", session.session_id, "html-test", input={"prompt": "Create an interactive tool"}
    )
    terminal = await container.worker.execute("tenant-a", run.run_id)
    p = (await container.presentations.materialize(terminal))[0]
    assert p.kind == "html"
    return container, p


@pytest.mark.asyncio
async def test_html_delivery_is_authenticated_scoped_and_sandboxed(
    html_fixture: tuple[ApiContainer, Presentation],
) -> None:
    container, p = html_fixture
    url = f"/v1/ui/presentations/{p.presentation_id}/html?revision=1"
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as client:
        r = await client.get(url, headers=H)
        assert r.status_code == 200 and r.content == CONTENT
        assert r.headers["content-type"].startswith("text/html")
        policy = r.headers["content-security-policy"]
        assert "sandbox allow-scripts" in policy and "allow-same-origin" not in policy
        assert "connect-src 'none'" in policy and "form-action 'none'" in policy
        assert r.headers["cache-control"] == "private, no-store"
        for headers in [{**H, "X-User-ID": "other"}, {**H, "X-Tenant-ID": "other"}]:
            assert (await client.get(url, headers=headers)).status_code == 404
        assert (
            await client.get(url.replace("revision=1", "revision=2"), headers=H)
        ).status_code == 404


@pytest.mark.asyncio
async def test_changed_object_bytes_are_not_executed(
    html_fixture: tuple[ApiContainer, Presentation], monkeypatch: pytest.MonkeyPatch
) -> None:
    container, p = html_fixture
    artifact = await container.artifacts.get("tenant-a", p.data["artifact_id"])

    async def changed(tenant_id: str, artifact_id: str) -> tuple[Artifact, bytes]:
        return artifact, CONTENT.replace(b"fixture", b"changed")

    monkeypatch.setattr(container.artifacts, "download", changed)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as client:
        r = await client.get(f"/v1/ui/presentations/{p.presentation_id}/html?revision=1", headers=H)
        assert r.status_code == 409
        assert b"<html>" not in r.content


@pytest.mark.asyncio
async def test_html_state_is_scoped_bounded_and_uses_existing_cas(
    html_fixture: tuple[ApiContainer, Presentation],
) -> None:
    container, p = html_fixture
    url = f"/v1/ui/presentations/{p.presentation_id}/view-state"
    body: dict[str, Any] = {
        "revision": 1,
        "expected_version": 0,
        "state": {"html_state": {"people": 8}},
    }
    invalid_states: list[dict[str, Any]] = [
        {"html_state": []},
        {"html_state": {"x": "a" * 16001}},
        {"action": "write"},
    ]
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as client:
        r = await client.put(url, headers=H, json=body)
        assert r.status_code == 200
        assert r.json()["state"] == {"html_state": {"people": 8}}
        assert (await client.put(url, headers=H, json=body)).status_code == 409
        assert (
            await client.put(url, headers={**H, "X-User-ID": "other"}, json=body)
        ).status_code == 404
        for state in invalid_states:
            r = await client.put(
                url, headers=H, json={**body, "expected_version": 1, "state": state}
            )
            assert r.status_code == 422
        restored = await client.get(f"/v1/ui/presentations/{p.presentation_id}", headers=H)
        assert restored.json()["view_state"]["state"] == {"html_state": {"people": 8}}


@pytest.mark.asyncio
async def test_restores_missing_publication_from_only_the_owned_terminal_run(
    html_fixture: tuple[ApiContainer, Presentation],
) -> None:
    from harness.presentation.repositories import InMemoryPresentationRepository

    container, p = html_fixture
    container.presentations.repository = InMemoryPresentationRepository()
    url = f"/v1/ui/presentations/{p.presentation_id}?revision=1&run_id={p.run_id}"
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as client:
        denied = await client.get(url, headers={**H, "X-User-ID": "other"})
        assert denied.status_code == 404
        assert await container.presentations.repository.for_run("tenant-a", p.run_id) is None
        recovered = await client.get(url, headers=H)
        assert recovered.status_code == 200
        assert recovered.json()["presentation_id"] == p.presentation_id
        document = await client.get(
            f"/v1/ui/presentations/{p.presentation_id}/html?revision=1", headers=H
        )
        assert document.content == CONTENT
        unrelated = await client.get(
            f"/v1/ui/presentations/ui_unknown?run_id={p.run_id}", headers=H
        )
        assert unrelated.status_code == 404


@pytest.mark.asyncio
async def test_does_not_publish_or_advertise_an_unfinished_run(
    html_fixture: tuple[ApiContainer, Presentation],
) -> None:
    from harness.agui.routes import (
        _run_projection,  # pyright: ignore[reportPrivateUsage] - exercise unfinished history projection
    )
    from harness.core.models import RunStatus

    container, p = html_fixture
    run = await container.runs.get("tenant-a", p.run_id)
    queued = await container.runs.create(
        "tenant-a", run.session_id, "pending-ui", input={"prompt": "pending"}
    )
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as client:
        pending = await client.get(
            f"/v1/ui/presentations/ui_pending?run_id={queued.run_id}", headers=H
        )
        assert pending.status_code == 425
        assert await container.presentations.repository.for_run("tenant-a", queued.run_id) is None
    # Even a complete model block and ready file are not advertised by an active
    # history until publication has finished for that run.
    active = run.model_copy(update={"status": RunStatus.RUNNING})
    projection = await _run_projection(container, "tenant-a", active, write_snapshot=False)
    assert projection.ui_blocks == []
