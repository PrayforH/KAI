from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from httpx import ASGITransport, AsyncClient

from harness.agui import routes as history_routes
from harness.agui.routes import _HISTORY_SNAPSHOT_VERSION as HISTORY_SNAPSHOT_VERSION
from harness.api.app import create_memory_app
from harness.api.dependencies import ApiContainer
from harness.assistant.models import MentionMessage
from harness.core.models import Run

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}


@pytest.mark.asyncio
async def test_main_history_hydrates_only_the_visible_receipt_and_skips_it_for_304() -> None:
    app = create_memory_app()
    now = datetime(2026, 11, 4, tzinfo=UTC)
    notices = [MentionMessage(
        message_id=f"receipt-{i}", text="", created_at=now + timedelta(seconds=i),
        run_id=f"child-{i}", target_thread_id="child-thread", source={
            "direction": "received", "runStatus": "succeeded",
        }) for i in range(2)]
    async def metadata(
        *_args: object, **kwargs: object,
    ) -> tuple[list[MentionMessage], int]:
        assert kwargs["hydrate_results"] is False
        return (notices, 2) if kwargs["direction"] == "received" else ([], 0)
    async def hydrate_message(_tenant: str, item: MentionMessage) -> MentionMessage:
        return item.model_copy(update={"text": "结果正文"})

    hydrate = AsyncMock(side_effect=hydrate_message)
    assistant = SimpleNamespace(
        repository=SimpleNamespace(conversation_notices=AsyncMock(return_value=([], 0)),
                                   conversation_notice_sources=AsyncMock(return_value={})),
        mention_messages=metadata, hydrate_mention_result=hydrate,
    )
    app.state.container = replace(app.state.container, assistant=assistant)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = "/v1/agui/threads/kai-personal-assistant-v1/history?limit=1"
        result = await client.get(path, headers=HEADERS)
        assert result.status_code == 200
        hydrate.assert_awaited_once_with("tenant-a", notices[1])
        assert _texts(result.json(), "assistant") == ["结果正文"]
        hydrate.reset_mock()
        unchanged = await client.get(
            path, headers={**HEADERS, "If-None-Match": result.headers["etag"]}
        )
        assert unchanged.status_code == 304
        hydrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_idle_history_validator_skips_projection_but_never_skips_ownership(monkeypatch):
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        await _drive_turn(app.state.container, "conditional-history", ["first"])
        path = "/v1/agui/threads/conditional-history/history?limit=1"
        first = await client.get(path, headers=HEADERS)
        assert first.status_code == 200
        tag = first.headers["etag"]
        reconstruct = AsyncMock(wraps=history_routes._history_messages_for_run)
        monkeypatch.setattr(history_routes, "_history_messages_for_run", reconstruct)
        unchanged = await client.get(path, headers={**HEADERS, "If-None-Match": tag})
        assert unchanged.status_code == 304 and unchanged.content == b""
        reconstruct.assert_not_awaited()
        compressed_validator = await client.get(
            path, headers={**HEADERS, "If-None-Match": f"W/{tag}"}
        )
        assert compressed_validator.status_code == 304
        reconstruct.assert_not_awaited()
        other = await client.get(
            path, headers={**HEADERS, "X-User-ID": "other", "If-None-Match": tag}
        )
        assert other.status_code in (403, 404)
        await _drive_turn(app.state.container, "conditional-history", ["first", "second"])
        changed = await client.get(path, headers={**HEADERS, "If-None-Match": tag})
        assert changed.status_code == 200 and changed.headers["etag"] != tag
        assert _texts(changed.json(), "user") == ["second"]


async def _drive_turn(container: ApiContainer, thread_id: str, prompts: list[str]) -> str:
    request = RunAgentInput(
        thread_id=thread_id,
        run_id=f"client-run-{len(prompts)}",
        messages=[
            UserMessage(id=f"msg-{index}", content=prompt) for index, prompt in enumerate(prompts)
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


async def _thread_runs(container: ApiContainer, thread_id: str) -> list[Run]:
    binding = await container.agui.get_binding(
        tenant_id="tenant-a", user_id="user-1", thread_id=thread_id
    )
    return await container.runs.list_for_sessions("tenant-a", list(binding.session_ids), limit=50)


def _texts(body: dict, role: str) -> list[str]:
    return [m["content"] for m in body["messages"] if m["role"] == role]


@pytest.mark.asyncio
async def test_history_paginates_runs_and_materializes_snapshots() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
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
        assert body["total"] == 3
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
        assert first_page["total"] == 3
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
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
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
        response = await client.get("/v1/agui/threads/thread-history-3/history", headers=HEADERS)
        assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_last", [False, True])
async def test_submit_from_partial_history_preserves_unloaded_prefix(edit_last: bool) -> None:
    app = create_memory_app()
    thread = "thread-paged-followup"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        for length in range(1, 5):
            await _drive_turn(container, thread, [f"turn {i}" for i in range(length)])
        page = (
            await client.get(f"/v1/agui/threads/{thread}/history?limit=2", headers=HEADERS)
        ).json()
        assert page["total"] == 4 and page["has_more"]
        messages = [message for message in page["messages"] if message["role"] == "user"]
        if edit_last:
            messages.pop()
        messages.append({"id": "new-client-message", "role": "user", "content": "new turn"})
        creation = await container.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput(
                thread_id=thread,
                run_id="new-client-run",
                messages=messages,
                tools=[],
                state={},
                context=[],
                forwarded_props={},
            ),
        )
        expected = [f"turn {i}" for i in range(3 if edit_last else 4)] + ["new turn"]
        assert creation.run.input["conversation_prompts"] == expected
        await container.worker.execute("tenant-a", creation.run.run_id)
        full = (
            await client.get(f"/v1/agui/threads/{thread}/history?limit=100", headers=HEADERS)
        ).json()
        assert _texts(full, "user") == expected
        assert full["total"] == len(expected)


@pytest.mark.asyncio
async def test_partial_history_anchor_cannot_import_another_threads_prefix() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        await _drive_turn(container, "other-thread", ["hidden", "shared"])
        foreign = (await _thread_runs(container, "other-thread"))[0]
        creation = await container.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput(
                thread_id="own-thread",
                run_id="new-client-run",
                messages=[
                    UserMessage(id=f"user-{foreign.run_id}", content="shared"),
                    UserMessage(id="new", content="followup"),
                ],
                tools=[],
                state={},
                context=[],
                forwarded_props={},
            ),
        )
        assert creation.run.input["conversation_prompts"] == ["shared", "followup"]


@pytest.mark.asyncio
async def test_legacy_history_rebuilds_web_images_and_reuses_the_new_snapshot() -> None:
    import json

    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        run_id = await _drive_turn(container, "thread-photo-history", ["查找广州街景图片"])
        runs = await _thread_runs(container, "thread-photo-history")
        run = next(item for item in runs if item.run_id == run_id)
        for event_type, payload in [
            (
                "tool.request",
                {
                    "tool_call_id": "search",
                    "name": "WebSearch",
                    "arguments": {"query": "广州街景图片"},
                },
            ),
            (
                "tool.result",
                {
                    "tool_call_id": "search",
                    "content": {
                        "images": [
                            {
                                "url": "https://cdn.example.com/photo.jpg",
                                "description": "广州街景",
                                "source_url": "https://news.example.com/story",
                            }
                        ]
                    },
                },
            ),
            (
                "history.snapshot",
                {
                    "version": HISTORY_SNAPSHOT_VERSION - 1,
                    "response_text": "旧快照",
                    "activity": None,
                },
            ),
        ]:
            await container.event_service.append(
                tenant_id="tenant-a",
                run_id=run_id,
                session_id=run.session_id,
                event_type=event_type,
                payload=payload,
            )
        for _ in range(2):
            response = await client.get(
                "/v1/agui/threads/thread-photo-history/history", headers=HEADERS
            )
            assert response.status_code == 200
            calls = [
                call
                for message in response.json()["messages"]
                for call in message.get("toolCalls", [])
            ]
            activity_call = next(
                call for call in calls if call["function"]["name"] == "harness_run_activity"
            )
            activity = json.loads(activity_call["function"]["arguments"])["activity"]
            images = [
                image
                for item in activity["items"]
                for image in item["metadata"].get("web_images", [])
            ]
            assert images[0]["url"] == "https://cdn.example.com/photo.jpg"
        snapshots = await container.observed_events.list_after(
            "tenant-a", run_id, 0, types=("history.snapshot",)
        )
        assert [snapshot.payload["version"] for snapshot in snapshots] == [
            HISTORY_SNAPSHOT_VERSION - 1,
            HISTORY_SNAPSHOT_VERSION,
        ]
