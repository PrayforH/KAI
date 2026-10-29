import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from harness.config import Settings
from harness.studio.builder_conversation import BuilderConversationRequest, BuilderMessage
from harness.studio.intent_router import BuilderIntentRouter, direct_builder_reply
from tests.unit.studio.test_service import create_request, studio


def completion(content: object, finish_reason: str = "stop") -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)},
                         "finish_reason": finish_reason}]}


@pytest.mark.asyncio
async def test_classification_uses_compact_context_and_no_tools_or_agent_model() -> None:
    draft = await studio().create(tenant_id="tenant-a", user_id="builder", request=create_request())
    captured: dict[str, Any] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.url == "http://gateway/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private-key"
        return httpx.Response(200, json=completion({"action": "run"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        router = BuilderIntentRouter(base_url="http://gateway/v1/", model="d1-3B",
            api_key=SecretStr("private-key"), http_client=client)
        request = BuilderConversationRequest(expectedRevision=1, intent="auto", messages=(
            BuilderMessage(role="user", content="把报告缩短"),
            BuilderMessage(role="assistant", content="修改默认行为还是只改本次报告？"),
            BuilderMessage(role="user", content="只改这次"),
        ), runContext='{"task":"原报告","status":"succeeded"}')
        decision = await router.classify(draft.spec, request)
    assert decision is not None and decision.action == "run"
    assert captured["model"] == "d1-3B"
    assert "tools" not in captured
    context = json.loads(captured["messages"][1]["content"])
    assert context["conversation"] == [m.model_dump() for m in request.messages]
    assert "systemPrompt" not in context["currentAgent"]
    assert "private-key" not in captured["messages"][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"action": "publish", "reply": "发布"},
    {"action": "run", "task": "未要求的字段"},
    {"action": "ask", "reply": "问题", "changes": {"description": "偷偷修改"}},
    {"action": "edit", "reply": "修改", "task": "测试"},
    {"action": "reply", "reply": "未要求的字段"},
])
async def test_invalid_decision_falls_back(payload: object) -> None:
    draft = await studio().create(tenant_id="tenant-a", user_id="builder", request=create_request())
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=completion(payload))
    )) as client:
        router = BuilderIntentRouter(base_url="http://gateway/v1", model="d1-3B",
            api_key=SecretStr(""), http_client=client)
        assert await router.classify(draft.spec, BuilderConversationRequest(
            expectedRevision=1, messages=(BuilderMessage(role="user", content="要求"),)
        )) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"error": "secret upstream"}),
    httpx.Response(200, json=completion({"action": "edit"}, "length")),
    httpx.Response(200, json={"choices": []}),
])
async def test_upstream_or_incomplete_response_falls_back(response: httpx.Response) -> None:
    draft = await studio().create(tenant_id="tenant-a", user_id="builder", request=create_request())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        router = BuilderIntentRouter(base_url="http://gateway/v1", model="d1-3B",
            api_key=SecretStr(""), http_client=client)
        assert await router.classify(draft.spec, BuilderConversationRequest(
            expectedRevision=1, messages=(BuilderMessage(role="user", content="要求"),)
        )) is None


@pytest.mark.asyncio
async def test_classifier_timeout_falls_back_and_cancellation_propagates() -> None:
    draft = await studio().create(tenant_id="tenant-a", user_id="builder", request=create_request())

    async def delayed(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(delayed)) as client:
        router = BuilderIntentRouter(base_url="http://gateway/v1", model="d1-3B",
            api_key=SecretStr(""), timeout_seconds=0.01, http_client=client)
        request = BuilderConversationRequest(expectedRevision=1,
            messages=(BuilderMessage(role="user", content="要求"),))
        assert await router.classify(draft.spec, request) is None
        task = asyncio.create_task(router.classify(draft.spec, request))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_classifier_is_disabled_by_default_and_timeout_is_bounded() -> None:
    assert BuilderIntentRouter.from_settings(Settings(studio_builder_intent_model="")) is None
    with pytest.raises(ValueError):
        Settings(studio_builder_intent_timeout_seconds=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("run_context,expected", [
    ("", "ask"),
    ('{"task":"上次任务"}', "rerun"),
    ('{"task":"上次任务","pendingProposal":{"baseRevision":1}}', "ask"),
])
async def test_rerun_requires_previous_task_and_no_pending_proposal(
    run_context: str, expected: str
) -> None:
    draft = await studio().create(tenant_id="tenant-a", user_id="builder", request=create_request())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, json=completion({"action": "rerun"})
    ))) as client:
        router = BuilderIntentRouter(base_url="http://gateway/v1", model="d1-3B",
            api_key=SecretStr(""), http_client=client)
        decision = await router.classify(draft.spec, BuilderConversationRequest(
            expectedRevision=1, messages=(BuilderMessage(role="user", content="重跑"),),
            runContext=run_context))
        assert decision is not None
        reply = direct_builder_reply(decision, run_context)
        assert reply is not None and reply.action == expected
