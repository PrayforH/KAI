import json
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from harness.api.app import create_app
from harness.api.dependencies import build_memory_container
from harness.config import Settings
from harness.studio.api import get_model_configuration_service
from harness.studio.intent_router import BuilderIntentDecision
from tests.integration.api.test_agent_studio_api import SERVICE_TOKEN, draft_request

HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-Tenant-ID": "tenant-intent",
    "X-User-ID": "builder-a",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("action,task,expected_calls", [
    ("run", "查这家公司的公开信息", 1),
    ("rerun", "", 0),
    ("ask", "", 0),
    ("edit", "", 1),
    ("reply", "", 1),
])
async def test_auto_classification_routes_without_changing_agent_model_or_draft(
    action: str, task: str, expected_calls: int
) -> None:
    container = build_memory_container(settings=Settings(studio_builder_intent_model="d1-3B"))
    application = create_app(replace(container, environment="production",
        api_bearer_token=SecretStr(SERVICE_TOKEN)))
    model = AsyncMock()
    model.complete_text.return_value = json.dumps({
        "action": action, "reply": "构建模型回复", "task": task, "changes": {}})
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    decision = BuilderIntentDecision.model_validate({
        "action": action})
    with patch("harness.studio.intent_router.BuilderIntentRouter.classify",
               new=AsyncMock(return_value=decision)) as classifier:
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as client:
            created = await client.post("/v1/studio/drafts", headers=HEADERS, json=draft_request())
            draft = created.json()
            path = f"/v1/studio/drafts/{draft['draftId']}"
            response = await client.post(path + "/builder-conversation", headers=HEADERS, json={
                "expectedRevision": 1, "intent": "auto",
                "messages": [{"role": "user", "content": "输入"}],
                "runContext": '{"task":"上次任务"}',
            })
            assert response.status_code == 200, response.text
            assert response.json()["action"] == action
            assert response.json()["task"] == task
            assert response.json()["changedFields"] == []
            assert (await client.get(path, headers=HEADERS)).json() == draft
            classifier.assert_awaited_once()
            assert model.complete_text.await_count == expected_calls
            if expected_calls:
                assert model.complete_text.call_args.args[1] == draft["spec"]["model"]["routeId"]
                assert f"意图为 {action}" in model.complete_text.call_args.kwargs["system_prompt"]
            forbidden = await client.post(path + "/builder-conversation",
                headers={**HEADERS, "X-User-ID": "other"}, json={
                    "expectedRevision": 1, "intent": "auto",
                    "messages": [{"role": "user", "content": "输入"}],
                })
            assert forbidden.status_code == 404
            assert classifier.await_count == 1


@pytest.mark.asyncio
async def test_explicit_edit_bypasses_classifier_and_auto_failure_uses_existing_model() -> None:
    application = create_app(replace(
        build_memory_container(settings=Settings(studio_builder_intent_model="d1-3B")),
        environment="production", api_bearer_token=SecretStr(SERVICE_TOKEN)))
    model = AsyncMock()
    model.complete_text.return_value = '{"action":"edit","reply":"预览","changes":{}}'
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    with patch("harness.studio.intent_router.BuilderIntentRouter.classify",
               new=AsyncMock(return_value=None)) as classifier:
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as client:
            draft = (await client.post("/v1/studio/drafts", headers=HEADERS,
                json=draft_request())).json()
            path = f"/v1/studio/drafts/{draft['draftId']}"
            body = {"expectedRevision": 1, "messages": [{"role": "user", "content": "输入"}]}
            explicit = await client.post(path + "/builder-conversation", headers=HEADERS,
                json={**body, "intent": "edit"})
            assert explicit.status_code == 200
            classifier.assert_not_awaited()
            automatic = await client.post(path + "/builder-conversation", headers=HEADERS,
                json={**body, "intent": "auto"})
            assert automatic.status_code == 200
            classifier.assert_awaited_once()
            assert "自动意图判断" in model.complete_text.call_args.kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_author_cannot_override_reply_classification_into_edit() -> None:
    application = create_app(replace(
        build_memory_container(settings=Settings(studio_builder_intent_model="d1-3B")),
        environment="production", api_bearer_token=SecretStr(SERVICE_TOKEN)))
    model = AsyncMock()
    model.complete_text.return_value = json.dumps({"action": "edit", "reply": "修改",
        "changes": {"description": "不该修改"}})
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    with patch("harness.studio.intent_router.BuilderIntentRouter.classify", new=AsyncMock(
        return_value=BuilderIntentDecision(action="reply")
    )):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as client:
            draft = (await client.post("/v1/studio/drafts", headers=HEADERS,
                json=draft_request())).json()
            path = f"/v1/studio/drafts/{draft['draftId']}"
            response = await client.post(path + "/builder-conversation", headers=HEADERS, json={
                "expectedRevision": 1, "intent": "auto",
                "messages": [{"role": "user", "content": "这个智能体能做什么"}],
            })
            assert response.status_code == 409
            assert (await client.get(path, headers=HEADERS)).json() == draft


@pytest.mark.asyncio
async def test_revision_change_during_classification_invalidates_run_decision() -> None:
    application = create_app(replace(
        build_memory_container(settings=Settings(studio_builder_intent_model="d1-3B")),
        environment="production", api_bearer_token=SecretStr(SERVICE_TOKEN)))
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (await client.post("/v1/studio/drafts", headers=HEADERS,
            json=draft_request())).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"

        async def meanwhile(*args: object, **kwargs: object) -> BuilderIntentDecision:
            draft["spec"]["description"] = "并发修改"
            changed = await client.put(path, headers=HEADERS,
                json={"expectedRevision": 1, "spec": draft["spec"]})
            assert changed.status_code == 200
            return BuilderIntentDecision(action="rerun")

        with patch("harness.studio.intent_router.BuilderIntentRouter.classify", new=meanwhile):
            response = await client.post(path + "/builder-conversation", headers=HEADERS, json={
                "expectedRevision": 1, "intent": "auto",
                "messages": [{"role": "user", "content": "输入"}],
                "runContext": '{"task":"上次任务"}',
            })
            assert response.status_code == 409
            model.complete_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_clarification_keeps_streaming_protocol() -> None:
    application = create_app(replace(
        build_memory_container(settings=Settings(studio_builder_intent_model="d1-3B")),
        environment="production", api_bearer_token=SecretStr(SERVICE_TOKEN)))
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    with patch("harness.studio.intent_router.BuilderIntentRouter.classify", new=AsyncMock(
        return_value=BuilderIntentDecision(action="ask")
    )):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            draft = (await client.post("/v1/studio/drafts", headers=HEADERS,
                json=draft_request())).json()
            response = await client.post(
                f"/v1/studio/drafts/{draft['draftId']}/builder-conversation",
                headers={**HEADERS, "Accept": "text/event-stream"},
                json={"expectedRevision": 1, "intent": "auto",
                      "messages": [{"role": "user", "content": "再短一点"}]},
            )
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines()
                      if line.startswith("data: ")]
            assert [item["type"] for item in events] == ["progress", "builder.reply", "result"]
            assert events[-1]["result"]["action"] == "ask"
            assert events[-1]["result"]["changedFields"] == []
            model.complete_text.assert_not_awaited()
