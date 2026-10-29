"""Reply references: persisted per run, replayed per history page, locatable by id."""

from typing import Any, cast

import pytest
from ag_ui.core import RunAgentInput, UserMessage
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.api.dependencies import ApiContainer
from tests.integration.api.test_agui_history_pagination import (
    FIXTURE_MANIFEST,
    HEADERS,
)


class _Thread:
    """Drives turns the way the real client does: cumulative messages per request."""

    def __init__(self, container: "ApiContainer", thread_id: str) -> None:
        self._container = container
        self.thread_id = thread_id
        self._prompts: list[str] = []
        self._counter = 0

    async def send(self, prompt: str, reply_to: dict[str, str] | None = None) -> str:
        self._counter += 1
        messages = [
            UserMessage(id=f"msg-{index}", content=item)
            for index, item in enumerate([*self._prompts, prompt])
        ]
        creation = await self._container.agui.create_run_with_result(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput(
                thread_id=self.thread_id,
                run_id=f"client-run-{self._counter}",
                messages=cast(Any, messages),
                tools=[],
                state=[],
                context=[],
                forwarded_props={"replyTo": reply_to} if reply_to is not None else {},
            ),
        )
        await self._container.worker.execute("tenant-a", creation.run.run_id)
        self._prompts.append(prompt)
        return creation.run.run_id


@pytest.mark.asyncio
async def test_reply_reference_persists_and_replays_per_page() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        thread = _Thread(container, "thread-reply-2")
        original_run = await thread.send("original")
        await thread.send("filler")
        reply_run = await thread.send(
            "把这个改成 PDF",
            {"messageId": f"user-{original_run}", "excerpt": "original"},
        )
        await thread.send("plain turn")

        full = await client.get(
            "/v1/agui/threads/thread-reply-2/history?limit=100", headers=HEADERS
        )
        assert full.status_code == 200
        body = full.json()
        assert body["replies"] == {
            f"user-{reply_run}": {"messageId": f"user-{original_run}", "excerpt": "original"}
        }, body.get("replies")

        # Pagination keeps the reference on the page that holds its message.
        page1 = await client.get(
            "/v1/agui/threads/thread-reply-2/history?limit=2", headers=HEADERS
        )
        assert page1.json()["replies"] == {
            f"user-{reply_run}": {"messageId": f"user-{original_run}", "excerpt": "original"}
        }
        page2 = await client.get(
            "/v1/agui/threads/thread-reply-2/history",
            params={"limit": 2, "before": page1.json()["next_cursor"]},
            headers=HEADERS,
        )
        assert page2.json().get("replies") in (None, {})

        # A thread without replies exposes no replies field at all.
        other = await client.get(
            "/v1/agui/threads/thread-reply-1/history?limit=100", headers=HEADERS
        )
        assert other.json().get("replies") is None


@pytest.mark.asyncio
async def test_invalid_reply_reference_is_rejected() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        request = RunAgentInput(
            thread_id="thread-reply-3",
            run_id="client-run-bad",
            messages=[UserMessage(id="m", content="hi")],
            tools=[],
            state=[],
            context=[],
            forwarded_props={"replyTo": {"messageId": ""}},
        )
        with pytest.raises(Exception, match="reply reference"):
            await container.agui.create_run_with_result(
                tenant_id="tenant-a",
                user_id="user-1",
                agent_name="echo-agent",
                agent_version="0.1.0",
                request=request,
            )


@pytest.mark.asyncio
async def test_message_context_resolves_quoted_original_by_id() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        container = app.state.container
        original = await _Thread(container, "thread-reply-4").send("original text here")

        found = await client.get(
            f"/v1/agui/threads/thread-reply-4/messages/user-{original}", headers=HEADERS
        )
        assert found.status_code == 200
        body = found.json()
        assert body["messageId"] == f"user-{original}"
        assert body["found"] is True
        assert body["runId"] == original
        assert body["excerpt"] == "original text here"
        assert body["role"] == "user"
        assert body["createdAt"]

        answer = await client.get(
            f"/v1/agui/threads/thread-reply-4/messages/assistant-{original}", headers=HEADERS
        )
        assert answer.json()["found"] is True
        assert answer.json()["role"] == "assistant"

        missing = await client.get(
            "/v1/agui/threads/thread-reply-4/messages/user-run-does-not-exist", headers=HEADERS
        )
        assert missing.status_code == 200
        assert missing.json() == {
            "messageId": "user-run-does-not-exist",
            "found": False,
        }
