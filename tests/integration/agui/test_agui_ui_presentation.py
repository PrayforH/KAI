"""Intelligent UI end to end: published live, and rebuilt identically on reload.

The echo runtime streams the prompt back, so a prompt carrying a ``harness-ui``
fence exercises the whole path — validator, durable events, live projection and
history reconstruction — without a model in the loop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}

TABLE_BLOCK = {
    "version": 1,
    "kind": "table",
    "title": "季度处理量",
    "fallback_text": "第一季度 120 件，第二季度 150 件。",
    "data": {
        "columns": [{"key": "quarter", "label": "季度"}, {"key": "count", "label": "数量"}],
        "rows": [{"quarter": "第一季度", "count": 120}, {"quarter": "第二季度", "count": 150}],
    },
}


def _request(*, thread_id: str, run_id: str, prompt: str) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": [{"id": f"message-{run_id}", "role": "user", "content": prompt}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def _events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _fenced_prompt(prefix: str) -> str:
    return f"{prefix}\n\n```harness-ui\n{json.dumps(TABLE_BLOCK, ensure_ascii=False)}\n```"


def _started_arguments(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The args a client aggregates from the streamed TOOL_CALL_ARGS deltas."""

    arguments: dict[str, str] = {}
    for event in events:
        if event.get("type") == "TOOL_CALL_ARGS":
            tool_call_id = str(event.get("toolCallId"))
            arguments[tool_call_id] = arguments.get(tool_call_id, "") + str(event.get("delta", ""))
    return [json.loads(value) for value in arguments.values()]


@pytest.mark.asyncio
async def test_publishes_an_interactive_block_live_and_on_reload() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(
                thread_id="thread-ui", run_id="client-run-ui", prompt=_fenced_prompt("请看对比")
            ),
            headers=HEADERS,
        )
        history = await client.get("/v1/agui/threads/thread-ui/history", headers=HEADERS)

    assert response.status_code == 200
    events = _events(response.text)
    tool_calls = [event for event in events if event.get("type") == "TOOL_CALL_START"]
    assert [event["toolCallName"] for event in tool_calls] == ["harness_present_ui"]
    custom = [event for event in events if event.get("name") == "harness.ui.v1"]
    assert len(custom) == 1

    published = _started_arguments(events)[0]
    assert published["kind"] == "table"
    assert published["run_id"] == response.headers["x-harness-run-id"]
    assert published["decision"]["origin"] == "model"
    assert custom[0]["value"] == published

    # The block must be published before the terminal frame, so a client never
    # finishes the run with the widget still to come.
    assert events.index(custom[0]) < len(events) - 1
    assert events[-1]["type"] == "RUN_FINISHED"

    assert history.status_code == 200
    restored = [
        call
        for message in history.json()["messages"]
        for call in cast(list[dict[str, Any]], message.get("toolCalls") or [])
        if call["function"]["name"] == "harness_present_ui"
    ]
    assert len(restored) == 1
    assert (
        json.loads(restored[0]["function"]["arguments"])["presentation_id"]
        == (published["presentation_id"])
    )


@pytest.mark.asyncio
async def test_an_plain_answer_publishes_no_block() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-plain", run_id="client-run-plain", prompt="hello"),
            headers=HEADERS,
        )
        history = await client.get("/v1/agui/threads/thread-plain/history", headers=HEADERS)

    events = _events(response.text)
    assert not [event for event in events if event.get("name") == "harness.ui.v1"]
    assert not [event for event in events if event.get("toolCallName") == "harness_present_ui"]
    assert not [
        call
        for message in history.json()["messages"]
        for call in cast(list[dict[str, Any]], message.get("toolCalls") or [])
        if call["function"]["name"] == "harness_present_ui"
    ]


@pytest.mark.asyncio
async def test_publishes_a_block_shaped_the_way_a_live_model_wrote_it() -> None:
    """A real 173 answer used `id` for dimensions and a positional `values` list.

    That shape is reproduced verbatim so it keeps rendering instead of regressing
    into raw JSON the next time someone tightens the schema.
    """

    live_shape = {
        "version": 1,
        "kind": "comparison",
        "title": "三平台对比",
        "fallback_text": "iOS 用 Swift，Android 用 Kotlin，HarmonyOS 用 ArkTS。",
        "data": {
            "dimensions": [
                {"id": "language", "label": "开发语言"},
                {"id": "privacy", "label": "权限"},
            ],
            "items": [
                {"label": "iPhone (iOS)", "values": ["Swift", "沙盒+强制授权"]},
                {"label": "Android", "values": ["Kotlin", "运行时权限"]},
                {"label": "HarmonyOS", "values": ["ArkTS", "场景化分级授权"]},
            ],
        },
    }
    prompt = f"对比三个平台\n\n```harness-ui\n{json.dumps(live_shape, ensure_ascii=False)}\n```"
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-live-shape", run_id="client-live", prompt=prompt),
            headers=HEADERS,
        )

    published = _started_arguments(_events(response.text))[0]
    assert published["kind"] == "comparison"
    assert published["decision"]["origin"] == "model"
    assert published["data"]["dimensions"] == [
        {"key": "language", "label": "开发语言", "align": None},
        {"key": "privacy", "label": "权限", "align": None},
    ]
    assert published["data"]["items"][0]["values"] == {
        "language": "Swift",
        "privacy": "沙盒+强制授权",
    }


@pytest.mark.asyncio
async def test_publishes_a_block_shaped_the_way_the_second_live_answer_wrote_it() -> None:
    """A later 173 answer used bare-string dimensions and named items with `key`.

    Both live shapes are kept verbatim: they are what the deployed model actually
    produces, and each one previously rendered as raw JSON.
    """

    live_shape = {
        "version": 1,
        "kind": "comparison",
        "title": "三方案评估对比",
        "fallback_text": "方案A成本120万；方案B成本90万；方案C成本150万。",
        "data": {
            "dimensions": ["成本", "周期", "风险"],
            "items": [
                {"key": "方案A", "values": {"成本": "120 万", "周期": "6 个月", "风险": "高"}},
                {"key": "方案B", "values": {"成本": "90 万", "周期": "8 个月", "风险": "中"}},
                {"key": "方案C", "values": {"成本": "150 万", "周期": "4 个月", "风险": "低"}},
            ],
        },
        "config": {},
    }
    prompt = f"对比三个方案\n\n```harness-ui\n{json.dumps(live_shape, ensure_ascii=False)}\n```"
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-live-shape-2", run_id="client-live-2", prompt=prompt),
            headers=HEADERS,
        )

    published = _started_arguments(_events(response.text))[0]
    assert published["kind"] == "comparison"
    assert [dimension["key"] for dimension in published["data"]["dimensions"]] == [
        "成本",
        "周期",
        "风险",
    ]
    assert [item["label"] for item in published["data"]["items"]] == ["方案A", "方案B", "方案C"]
    assert published["data"]["items"][0]["values"]["成本"] == "120 万"


@pytest.mark.asyncio
async def test_capabilities_endpoint_describes_the_catalog() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/ui/capabilities", headers=HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "harness.ui/v1"
    assert payload["max_presentations_per_turn"] == 2
    assert payload["generated_html"] is False
    assert {renderer["kind"] for renderer in payload["renderers"]} == {
        "table",
        "comparison",
        "chart",
        "diagram",
        "timeline",
        "metric",
        "form",
    }
