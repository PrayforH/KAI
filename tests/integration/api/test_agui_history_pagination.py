from pathlib import Path

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from httpx import ASGITransport, AsyncClient

from harness.agui.routes import _HISTORY_SNAPSHOT_VERSION as HISTORY_SNAPSHOT_VERSION
from harness.api.app import create_memory_app

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


async def _drive_turn(container, thread_id: str, prompts: list[str]) -> str:
    request = RunAgentInput(
        thread_id=thread_id,
        run_id=f"client-run-{len(prompts)}",
        messages=[
            UserMessage(id=f"msg-{index}", content=prompt)
            for index, prompt in enumerate(prompts)
        ],
        tools=[],
        state=[],
        context=[],
        forwarded_props={},
    )
    creation = await container.agui.create_run_with_result(
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=request,
    )
    run_id = creation.run.run_id
    await container.worker.execute("tenant-a", run_id)
    return run_id


async def _thread_runs(container, thread_id: str):
    binding = await container.agui.get_binding(
        tenant_id="tenant-a", user_id="user-1", thread_id=thread_id
    )
    return await container.runs.list_for_sessions(
        "tenant-a", list(binding.session_ids), limit=50
    )


def _texts(body: dict, role: str) -> list[str]:
    return [m["content"] for m in body["messages"] if m["role"] == role]


@pytest.mark.asyncio
async def test_history_paginates_runs_and_materializes_snapshots() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS
        )
        container = app.state.container
        prompts: list[str] = []
        run_ids: list[str] = []
        for turn in ("first", "second", "third"):
            prompts.append(f"turn {turn}")
            run_ids.append(await _drive_turn(container, "thread-history-1", prompts))

        full = await client.get(
            "/v1/agui/threads/thread-history-1/history?limit=100", headers=HEADERS
        )
        assert full.status_code == 200
        body = full.json()
        assert body["has_more"] is False
        assert body.get("next_cursor") is None
        assert _texts(body, "user") == ["turn first", "turn second", "turn third"]
        assert _texts(body, "assistant") == [
            "Echo: turn first",
            "Echo: turn second",
            "Echo: turn third",
        ]

        page1 = await client.get(
            "/v1/agui/threads/thread-history-1/history?limit=2", headers=HEADERS
        )
        assert page1.status_code == 200
        first_page = page1.json()
        assert first_page["has_more"] is True
        assert first_page["next_cursor"]
        assert _texts(first_page, "user") == ["turn second", "turn third"]
        assert first_page["status"] == body["status"]
        assert first_page["run_id"] == body["run_id"]

        page2 = await client.get(
            "/v1/agui/threads/thread-history-1/history",
            params={"limit": 2, "before": first_page["next_cursor"]},
            headers=HEADERS,
        )
        assert page2.status_code == 200
        second_page = page2.json()
        assert second_page["has_more"] is False
        assert _texts(second_page, "user") == ["turn first"]

        runs = await _thread_runs(container, "thread-history-1")
        assert len(runs) == 3
        for run in runs:
            snapshots = await container.observed_events.list_after(
                "tenant-a", run.run_id, 0, types=("history.snapshot",)
            )
            assert len(snapshots) == 1
            assert snapshots[0].payload["version"] == HISTORY_SNAPSHOT_VERSION
            expected_turn = run.input["prompt"].split()[-1]
            assert snapshots[0].payload["response_text"] == f"Echo: turn {expected_turn}"

        replay = await client.get(
            "/v1/agui/threads/thread-history-1/history?limit=100", headers=HEADERS
        )
        assert replay.status_code == 200
        assert replay.json()["messages"] == body["messages"]
        for run in runs:
            snapshots = await container.observed_events.list_after(
                "tenant-a", run.run_id, 0, types=("history.snapshot",)
            )
            assert len(snapshots) == 1


@pytest.mark.asyncio
async def test_history_rejects_invalid_cursor() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS
        )
        container = app.state.container
        await _drive_turn(container, "thread-history-2", ["seed"])
        response = await client.get(
            "/v1/agui/threads/thread-history-2/history",
            params={"before": "not-a-cursor"},
            headers=HEADERS,
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_history_keeps_unread_thread_empty() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/agui/threads/thread-history-3/history", headers=HEADERS
        )
        assert response.status_code == 404
