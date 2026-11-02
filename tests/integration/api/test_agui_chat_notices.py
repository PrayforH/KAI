"""Background results are real owner-scoped main-chat messages, not fake Runs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.assistant.chat_notices import notice_message_id, notice_text
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
from harness.assistant.models import AssistantInboxItem
from tests.integration.api.test_agui_history_pagination import (
    FIXTURE_MANIFEST,
    HEADERS,
    _drive_turn,
)


def notice(item_id="result-a", **changes):
    return AssistantInboxItem.model_validate(
        {
            "itemId": item_id,
            "objectiveId": "objective-a",
            "kind": "failed",
            "title": "网页报告",
            "content": "下载超时，文件仍然保留。",
            "createdAt": datetime.now(UTC),
            "threadId": "assistant_specialist_report",
            "runId": "source-run",
            "executionId": "execution-a",
            **changes,
        }
    )


def attach(app, items):
    async def page(tenant, user, *, before=None, limit=100):
        owned = items if (tenant, user) == ("tenant-a", "user-1") else []
        ordered = sorted(owned, key=lambda n: (n.created_at, notice_message_id(n)), reverse=True)
        return [
            n for n in ordered if before is None or (n.created_at, notice_message_id(n)) < before
        ][:limit], len(owned)

    async def one(tenant, user, item_id):
        return (
            next((n for n in items if n.item_id == item_id), None)
            if (tenant, user) == ("tenant-a", "user-1")
            else None
        )

    repo = SimpleNamespace(
        conversation_notices=AsyncMock(side_effect=page),
        conversation_notice=AsyncMock(side_effect=one),
    )
    app.state.container = replace(app.state.container, assistant=SimpleNamespace(repository=repo))
    return repo


@pytest.mark.asyncio
async def test_main_history_interleaves_results_with_turns_pages_and_survives_read():
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        run_id = await _drive_turn(container, MAIN, ["安排报告"])
        run = await container.runs.get("tenant-a", run_id)
        items = [
            notice("older", createdAt=run.created_at - timedelta(seconds=1)),
            notice("newer", createdAt=run.created_at + timedelta(seconds=1)),
        ]
        attach(app, items)
        url = f"/v1/agui/threads/{MAIN}/history"
        first = (await client.get(url, params={"limit": 2}, headers=HEADERS)).json()
        assert [m["id"] for m in first["messages"] if m["role"] != "tool"] == [
            f"user-{run_id}",
            f"assistant-{run_id}",
            "notice-newer",
        ]
        assert first["run_id"] == run_id and first["status"] == "succeeded"
        assert first["has_more"] and first["total"] == 3
        second = (
            await client.get(
                url, params={"limit": 2, "before": first["next_cursor"]}, headers=HEADERS
            )
        ).json()
        assert [m["id"] for m in second["messages"]] == ["notice-older"]
        assert not second["has_more"]
        items[1] = items[1].model_copy(update={"read_at": datetime.now(UTC)})
        assert (await client.get(url, params={"limit": 2}, headers=HEADERS)).json()[
            "messages"
        ] == first["messages"]
        assert (
            len(await container.runs.list_for_sessions("tenant-a", [run.session_id], limit=20)) == 1
        )


@pytest.mark.asyncio
async def test_notice_before_first_chat_and_quote_context_are_private():
    app = create_memory_app()
    items = [notice()]
    attach(app, items)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        url = f"/v1/agui/threads/{MAIN}"
        history = (await client.get(url + "/history", headers=HEADERS)).json()
        assert history["status"] == "idle" and "run_id" not in history
        assert history["messages"] == [
            {"id": "notice-result-a", "role": "assistant", "content": notice_text(items[0])}
        ]
        context = (await client.get(url + "/messages/notice-result-a", headers=HEADERS)).json()
        assert context["found"] and context["role"] == "assistant" and "runId" not in context
        for owner in ({**HEADERS, "X-User-ID": "other"}, {**HEADERS, "X-Tenant-ID": "other"}):
            assert not (await client.get(url + "/messages/notice-result-a", headers=owner)).json()[
                "found"
            ]
            assert (await client.get(url + "/history", headers=owner)).json()["messages"] == []
        assert not (
            await client.get("/v1/agui/threads/other/messages/notice-result-a", headers=HEADERS)
        ).json()["found"]


@pytest.mark.asyncio
async def test_quoting_notice_resolves_server_text_not_client_excerpt():
    app = create_memory_app(auto_execute=False)
    attach(app, [notice()])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        body = {
            "threadId": MAIN,
            "runId": "quoted-result",
            "messages": [{"id": "m", "role": "user", "content": "这是什么情况？"}],
            "tools": [],
            "state": {},
            "context": [],
            "forwardedProps": {
                "replyTo": {"messageId": "notice-result-a", "excerpt": "FORGED SUCCESS"}
            },
        }
        # Stop before streaming an unexecuted Run; inspect the created Run through
        # a capturing service seam, while retaining all real request validation.
        create = app.state.container.agui.create_run_with_result
        captured = []

        async def capture(**kwargs):
            result = await create(**kwargs)
            captured.append(result.run)
            raise RuntimeError("captured before stream")

        app.state.container.agui.create_run_with_result = capture
        with pytest.raises(RuntimeError, match="captured before stream"):
            await client.post(
                "/v1/agui?agent_name=echo-agent&agent_version=0.1.0", json=body, headers=HEADERS
            )
        context = captured[0].input["quoted_message_context"]
        assert "下载超时" in context and "source-run" in context and "FORGED SUCCESS" not in context
        body["forwardedProps"]["replyTo"]["messageId"] = "notice-missing"
        denied = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0", json=body, headers=HEADERS
        )
        assert denied.status_code == 409
