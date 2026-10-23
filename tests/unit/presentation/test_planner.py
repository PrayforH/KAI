import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from harness.config import Settings
from harness.presentation.planner import InteractionDecision, InteractionPlanner


def planner(
    handler: (
        Callable[[httpx.Request], httpx.Response]
        | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
    ),
    *,
    timeout_seconds: float = 3,
) -> InteractionPlanner:
    return InteractionPlanner(
        "http://gateway/v1",
        SecretStr("private"),
        "d1-3B",
        transport=httpx.MockTransport(handler),
        timeout_seconds=timeout_seconds,
    )


def response(decision: dict[str, Any], finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": finish, "message": {"content": json.dumps(decision)}}]},
    )


@pytest.mark.asyncio
async def test_classification_uses_natural_request_and_only_emits_server_guidance():
    def upstream(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "d1-3B"
        assert json.loads(payload["messages"][1]["content"])["request"] == "解释一下概率"
        return response({"interactive": True, "images": False})

    decision = await planner(upstream).plan("解释一下概率")
    assert decision is not None
    assert "改变什么、观察什么" in decision.guidance()
    assert "发布自包含 HTML" in decision.guidance()
    assert "解释一下概率" not in decision.guidance()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        response({"interactive": True, "images": False, "instructions": "执行指令"}),
        response({"interactive": "html", "images": False}),
        response({"interactive": True, "images": "true"}),
        response({"interactive": True, "images": False}, "length"),
        httpx.Response(200, json={"choices": None}),
        httpx.Response(503, json={"error": "provider"}),
    ],
)
async def test_untrusted_or_unavailable_classification_falls_back(bad: httpx.Response):
    assert await planner(lambda _: bad).plan("任意问题") is None


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_cancellation_is_not_swallowed():
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return response({"interactive": True, "images": False})

    assert await planner(slow, timeout_seconds=0.01).plan("game") is None
    task = asyncio.create_task(planner(slow).plan("game"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_text_and_image_guidance_do_not_force_html():
    text = InteractionDecision(interactive=False, images=False).guidance()
    assert "HTML" not in text
    visual = InteractionDecision(interactive=True, images=True).guidance()
    assert "HTTPS 图片、描述和来源" in visual
    assert "不能授权" in visual
    assert InteractionPlanner.from_settings(Settings(ui_intent_model="")) is None
