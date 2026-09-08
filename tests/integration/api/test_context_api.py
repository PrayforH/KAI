import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ag_ui.core import RunAgentInput
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.context.models import ContextDigestCreator, ContextDigestEntry, ContextDigestSource
from harness.core.events import RunEvent
from harness.policy.models import ContextTrust

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
OWNER_HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "owner-a"}
OTHER_HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "owner-b"}


def _agui_request() -> dict[str, object]:
    return {
        "threadId": "thread-context",
        "runId": "client-run-context",
        "state": {},
        "messages": [{"id": "message-context", "role": "user", "content": "hello"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


async def _create_digest(app: FastAPI, session_id: str, version: int) -> str:
    container = app.state.container
    digest = await container.context.create_digest(
        tenant_id="tenant-a",
        owner_user_id="owner-a",
        session_id=session_id,
        source=ContextDigestSource(
            sdk_session_id_hash=f"sha256:{'a' * 64}",
            through_run_id=f"run-{version}",
            through_event_sequence=version,
            transcript_checkpoint_hash=f"sha256:{str(version) * 64}",
        ),
        created_by=ContextDigestCreator(
            route_id="context-digest-v1",
            model="deterministic",
            prompt_revision="context-digest-v1",
        ),
        facts=(
            ContextDigestEntry(
                text=f"recovery point {version}",
                source_refs=(f"run:run-{version}:event:{version}",),
                trust=ContextTrust.SAFE,
            ),
        ),
    )
    return digest.digest_id


@pytest.mark.asyncio
async def test_session_context_api_is_owner_scoped_and_cursor_paginated() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=OWNER_HEADERS,
        )
        session = await client.post(
            "/v1/sessions",
            json={"agent_name": "echo-agent", "agent_version": "0.1.0"},
            headers=OWNER_HEADERS,
        )
        session_id = session.json()["session_id"]

        empty = await client.get(f"/v1/sessions/{session_id}/context", headers=OWNER_HEADERS)
        digest_ids = [await _create_digest(app, session_id, version) for version in range(1, 4)]
        first = await client.get(
            f"/v1/sessions/{session_id}/context?limit=2", headers=OWNER_HEADERS
        )
        second = await client.get(
            f"/v1/sessions/{session_id}/context?limit=2&before_version=2",
            headers=OWNER_HEADERS,
        )
        digest = await client.get(
            f"/v1/sessions/{session_id}/context/digests/{digest_ids[-1]}",
            headers=OWNER_HEADERS,
        )
        hidden = await client.get(f"/v1/sessions/{session_id}/context", headers=OTHER_HEADERS)

    assert empty.status_code == 200
    assert empty.json() == {
        "session_id": session_id,
        "state": None,
        "digests": [],
        "next_before_version": None,
        "window": None,
        "window_status": {
            "status": "pending",
            "checked_at": None,
            "source_run_id": None,
            "reason": None,
        },
    }
    assert first.status_code == 200
    assert [item["version"] for item in first.json()["digests"]] == [3, 2]
    assert first.json()["next_before_version"] == 2
    assert [item["version"] for item in second.json()["digests"]] == [1]
    assert second.json()["next_before_version"] is None
    assert digest.status_code == 200
    assert digest.json()["digest_id"] == digest_ids[-1]
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_agui_thread_context_uses_existing_authenticated_thread_boundary() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=OWNER_HEADERS,
        )
        run = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_agui_request(),
            headers=OWNER_HEADERS,
        )
        binding = await app.state.container.agui.get_binding(
            tenant_id="tenant-a",
            user_id="owner-a",
            thread_id="thread-context",
        )
        await _create_digest(app, binding.session_id, 1)
        latest_run = (
            await app.state.container.runs.list_for_sessions(
                "tenant-a", [binding.session_id], limit=1
            )
        )[0]
        await app.state.container.events.append(
            RunEvent(
                event_id="event-context-window",
                tenant_id="tenant-a",
                run_id=latest_run.run_id,
                session_id=binding.session_id,
                sequence=(
                    await app.state.container.events.latest_sequence("tenant-a", latest_run.run_id)
                    + 1
                ),
                type="context.window.observed",
                timestamp=datetime.now(UTC),
                payload={
                    "phase": "after",
                    "total_tokens": 135_000,
                    "max_tokens": 180_000,
                    "raw_max_tokens": 200_000,
                    "percentage": 75.0,
                    "model": "claude-sonnet",
                    "auto_compact_enabled": True,
                    "auto_compact_threshold": 175_000,
                    "categories": [{"name": "Messages", "tokens": 135_000}],
                },
            )
        )
        context = await client.get("/v1/agui/threads/thread-context/context", headers=OWNER_HEADERS)
        await app.state.container.events.append(
            RunEvent(
                event_id="event-context-window-unavailable",
                tenant_id="tenant-a",
                run_id=latest_run.run_id,
                session_id=binding.session_id,
                sequence=(
                    await app.state.container.events.latest_sequence("tenant-a", latest_run.run_id)
                    + 1
                ),
                type="context.window.unavailable",
                timestamp=datetime.now(UTC),
                payload={"phase": "after", "reason": "control_timeout"},
            )
        )
        unavailable = await client.get(
            "/v1/agui/threads/thread-context/context", headers=OWNER_HEADERS
        )
        hidden = await client.get("/v1/agui/threads/thread-context/context", headers=OTHER_HEADERS)

    assert run.status_code == 200
    assert context.status_code == 200
    assert context.json()["session_id"] == binding.session_id
    assert [item["version"] for item in context.json()["digests"]] == [1]
    assert context.json()["window"] == {
        "source_run_id": latest_run.run_id,
        "observed_at": context.json()["window"]["observed_at"],
        "phase": "after",
        "total_tokens": 135_000,
        "max_tokens": 180_000,
        "raw_max_tokens": 200_000,
        "headroom_tokens": 45_000,
        "percentage": 75.0,
        "model": "claude-sonnet",
        "auto_compact_enabled": True,
        "auto_compact_threshold": 175_000,
        "provider_threshold_percentage": pytest.approx(97.2222, rel=1e-4),
        "categories": [{"name": "Messages", "tokens": 135_000}],
        "level": "compact_ready",
        "soft_threshold_percentage": 65.0,
        "compact_ready_percentage": 75.0,
        "hard_threshold_percentage": 85.0,
        "recommended_action": "consider_rebase",
    }
    assert context.json()["window_status"] == {
        "status": "available",
        "checked_at": context.json()["window"]["observed_at"],
        "source_run_id": latest_run.run_id,
        "reason": None,
    }
    assert unavailable.json()["window"] is None
    assert unavailable.json()["window_status"] == {
        "status": "unavailable",
        "checked_at": unavailable.json()["window_status"]["checked_at"],
        "source_run_id": latest_run.run_id,
        "reason": "control_timeout",
    }
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_agui_context_rebase_is_recoverable_owner_scoped_and_blocks_active_runs() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=OWNER_HEADERS,
        )
        initial = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_agui_request(),
            headers=OWNER_HEADERS,
        )
        binding = await app.state.container.agui.get_binding(
            tenant_id="tenant-a",
            user_id="owner-a",
            thread_id="thread-context",
        )
        source = await app.state.container.sessions.get("tenant-a", binding.session_id)
        await app.state.container.worker._sessions.bind_claude_session_id(  # noqa: SLF001
            "tenant-a", source.session_id, "sdk-source-session"
        )
        await _create_digest(app, source.session_id, 1)

        supported = await client.get(
            "/v1/agui/threads/thread-context/context", headers=OWNER_HEADERS
        )
        hidden = await client.post(
            "/v1/agui/threads/thread-context/context/rebase", headers=OTHER_HEADERS
        )
        rebased = await client.post(
            "/v1/agui/threads/thread-context/context/rebase", headers=OWNER_HEADERS
        )
        rebase_payload = rebased.json()
        target = await app.state.container.sessions.get("tenant-a", rebase_payload["session_id"])
        after = await client.get("/v1/agui/threads/thread-context/context", headers=OWNER_HEADERS)
        projection = await app.state.container.context.recovery_projection(
            "tenant-a", "owner-a", target.session_id
        )

        active = await app.state.container.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="owner-a",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput.model_validate(
                {
                    **_agui_request(),
                    "runId": "client-run-after-rebase",
                    "messages": [
                        {
                            "id": "message-after-rebase",
                            "role": "user",
                            "content": "continue",
                        }
                    ],
                }
            ),
        )
        blocked = await client.post(
            "/v1/agui/threads/thread-context/context/rebase/rollback",
            headers=OWNER_HEADERS,
        )
        await app.state.container.runs.cancel("tenant-a", active.run.run_id)
        rolled_back = await client.post(
            "/v1/agui/threads/thread-context/context/rebase/rollback",
            headers=OWNER_HEADERS,
        )

    assert initial.status_code == 200
    assert supported.status_code == 200
    assert supported.json()["rebase_supported"] is True
    assert hidden.status_code == 404
    assert rebased.status_code == 200
    assert rebase_payload["previous_session_id"] == source.session_id
    assert target.session_id.startswith("session_ctx_")
    assert target.claude_session_id is None
    assert target.model_dump(exclude={"session_id", "claude_session_id", "created_at"}) == (
        source.model_dump(exclude={"session_id", "claude_session_id", "created_at"})
    )
    assert after.json()["previous_session_count"] == 1
    assert after.json()["rollback_supported"] is True
    assert "recovery point 1" in projection
    assert blocked.status_code == 409
    assert rolled_back.status_code == 200
    assert rolled_back.json()["session_id"] == source.session_id


@pytest.mark.asyncio
async def test_run_creation_retries_on_session_rebase_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents",
            json={"path": str(FIXTURE_MANIFEST)},
            headers=OWNER_HEADERS,
        )
        await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_agui_request(),
            headers=OWNER_HEADERS,
        )
        container = app.state.container
        source_binding = await container.agui.get_binding(
            tenant_id="tenant-a",
            user_id="owner-a",
            thread_id="thread-context",
        )
        await container.worker._sessions.bind_claude_session_id(  # noqa: SLF001
            "tenant-a", source_binding.session_id, "sdk-source-session"
        )
        await _create_digest(app, source_binding.session_id, 1)

        original_create = container.agui._run_service.create_with_result  # noqa: SLF001
        create_entered = asyncio.Event()
        continue_create = asyncio.Event()
        call_count = 0

        async def delayed_create(*args: object, **kwargs: object):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                create_entered.set()
                await continue_create.wait()
            return await original_create(*args, **kwargs)

        monkeypatch.setattr(
            container.agui._run_service,  # noqa: SLF001
            "create_with_result",
            delayed_create,
        )
        request = RunAgentInput.model_validate(
            {
                **_agui_request(),
                "runId": "client-run-racing-rebase",
                "messages": [
                    {
                        "id": "message-racing-rebase",
                        "role": "user",
                        "content": "continue after rebase",
                    }
                ],
            }
        )
        creation_task = asyncio.create_task(
            container.agui.create_run_with_result(
                tenant_id="tenant-a",
                user_id="owner-a",
                agent_name="echo-agent",
                agent_version="0.1.0",
                request=request,
            )
        )
        await asyncio.wait_for(create_entered.wait(), timeout=1)
        rebased = await container.agui.rebase_context(
            tenant_id="tenant-a",
            user_id="owner-a",
            thread_id="thread-context",
        )
        continue_create.set()
        creation = await asyncio.wait_for(creation_task, timeout=1)
        current = await container.agui.get_binding(
            tenant_id="tenant-a",
            user_id="owner-a",
            thread_id="thread-context",
        )
        old_runs = await container.runs.list_for_sessions("tenant-a", [source_binding.session_id])
        await container.runs.cancel("tenant-a", creation.run.run_id)

    assert call_count == 2
    assert current.session_id == rebased.session_id
    assert creation.run.session_id == rebased.session_id
    assert any(
        run.idempotency_key == "client-run-racing-rebase" and run.status.value == "cancelled"
        for run in old_runs
    )
