import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from harness.studio.api import get_model_configuration_service
from tests.integration.api.test_agent_studio_api import SERVICE_TOKEN, app, draft_request


@pytest.mark.asyncio
async def test_multi_turn_preview_apply_conflict_and_scope() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        original = draft["spec"]
        model.complete_text.return_value = json.dumps(
            {
                "reply": "将结果改为表格，等待确认。",
                "changes": {"systemPrompt": original["systemPrompt"] + "\n输出表格。"},
            }
        )
        messages = [{"role": "user", "content": "输出改为表格"}]
        preview = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 1, "messages": messages},
        )
        assert preview.status_code == 200, preview.text
        proposal = preview.json()
        assert proposal["changedFields"] == ["systemPrompt"]
        assert list(proposal["changes"]) == ["systemPrompt"]  # No null defaults on the wire.
        assert (await client.get(path, headers=headers)).json() == draft
        applied = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert applied.status_code == 200, applied.text
        saved = applied.json()
        assert saved["draftId"] == draft["draftId"]
        assert saved["revision"] == 2
        assert saved["publishedVersion"] is None
        assert saved["spec"]["skills"] == original["skills"]
        assert saved["spec"]["model"] == original["model"]
        stale = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 1, "changes": proposal["changes"]},
        )
        assert stale.status_code == 409
        messages += [
            {"role": "assistant", "content": "已改为表格"},
            {"role": "user", "content": "再简短一点，最多三行"},
        ]
        model.complete_text.return_value = json.dumps(
            {
                "reply": "压缩为三行表格。",
                "changes": {"systemPrompt": saved["spec"]["systemPrompt"] + "\n最多三行。"},
            }
        )
        second = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 2, "messages": messages},
        )
        assert second.status_code == 200, second.text
        model_context = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
        assert model_context["currentDraft"]["systemPrompt"] == saved["spec"]["systemPrompt"]
        assert len(model_context["conversation"]) == 3
        second_saved = await client.post(
            path + "/builder-apply",
            headers=headers,
            json={"expectedRevision": 2, "changes": second.json()["changes"]},
        )
        assert second_saved.status_code == 200
        assert second_saved.json()["revision"] == 3
        assert "输出表格" in second_saved.json()["spec"]["systemPrompt"]
        assert "最多三行" in second_saved.json()["spec"]["systemPrompt"]
        assert len((await client.get("/v1/studio/drafts", headers=headers)).json()) == 1
        forbidden = await client.post(
            path + "/builder-conversation",
            headers={**headers, "X-User-ID": "other"},
            json={"expectedRevision": 3, "messages": messages},
        )
        assert forbidden.status_code == 404
        for changes, status in [
            ({"executionProfile": "local"}, 422),
            ({"systemPrompt": None}, 422),
            ({"builtinTools": ["NotARegisteredTool"]}, 409),
            ({"mcpServers": ["new-external-server"]}, 409),
            ({"skillInstructions": [{"name": "missing", "instructions": "changed"}]}, 409),
            ({"roleResponsibilities": [{"alias": "missing", "responsibility": "changed"}]}, 409),
        ]:
            response = await client.post(
                path + "/builder-apply",
                headers=headers,
                json={"expectedRevision": 3, "changes": changes},
            )
            assert response.status_code == status, response.text
        assert (await client.get(path, headers=headers)).json()["revision"] == 3
        model.complete_text.return_value = "not json"
        invalid = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 3, "messages": messages},
        )
        assert invalid.status_code == 409
        model.complete_text.return_value = '{"reply":"请说明输出格式","changes":{}}'
        clarified = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 3, "messages": messages},
        )
        assert clarified.json()["changedFields"] == []


@pytest.mark.asyncio
async def test_edit_during_generation_invalidates_proposal() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"

        async def meanwhile(*args: object, **kwargs: object) -> str:
            draft["spec"]["description"] = "手动编辑的内容"
            response = await client.put(
                path, headers=headers, json={"expectedRevision": 1, "spec": draft["spec"]}
            )
            assert response.status_code == 200
            return '{"reply":"已准备建议","changes":{"description":"模型的新建议"}}'

        model.complete_text.side_effect = meanwhile
        response = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={"expectedRevision": 1, "messages": [{"role": "user", "content": "修改简介"}]},
        )
        assert response.status_code == 409
        assert (await client.get(path, headers=headers)).json()["spec"][
            "description"
        ] == "手动编辑的内容"


@pytest.mark.asyncio
async def test_auto_intents_are_read_only_structured_and_keep_clarification_context() -> None:
    application = app()
    model = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        messages = [{"role": "user", "content": "再短一点"}]
        for action, task, text in [
            ("ask", "", "是修改默认行为还是只改本次结果？"),
            ("run", "根据原始材料输出三行摘要", "测试三行摘要"),
            ("rerun", "", "重新执行上次任务"),
            ("reply", "", "当前配置使用结构化输出"),
        ]:
            model.complete_text.return_value = json.dumps(
                {
                    "reply": text,
                    "action": action,
                    "task": task,
                    "changes": {},
                }
            )
            response = await client.post(
                path + "/builder-conversation",
                headers=headers,
                json={
                    "intent": "auto",
                    "expectedRevision": 1,
                    "messages": messages,
                    "runContext": '{"task":"原始材料摘要","status":"succeeded"}',
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["action"] == action
            assert response.json()["task"] == task
            assert response.json()["changedFields"] == []
            assert (await client.get(path, headers=headers)).json() == draft
            context = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
            assert context["conversation"] == messages
            assert "自动意图判断" in model.complete_text.call_args.kwargs["system_prompt"]
            messages.extend(
                [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": "只改本次结果"},
                ]
            )

        for payload in [
            {"reply": "开始", "action": "run", "changes": {}},  # Missing executable task.
            {"reply": "开始", "action": "run", "task": "测试", "changes": {"description": "偷改"}},
            {"reply": "请说明", "action": "ask", "changes": {"description": "偷改"}},
            {"reply": "已发布", "action": "publish", "changes": {}},
            {"reply": "未指定意图", "changes": {}},
        ]:
            model.complete_text.return_value = json.dumps(payload)
            response = await client.post(
                path + "/builder-conversation",
                headers=headers,
                json={
                    "intent": "auto",
                    "expectedRevision": 1,
                    "messages": messages,
                },
            )
            assert response.status_code == 409, response.text
        assert (await client.get(path, headers=headers)).json() == draft

        model.complete_text.return_value = json.dumps(
            {
                "reply": "测试",
                "action": "run",
                "task": "测试",
                "changes": {},
            }
        )
        response = await client.post(
            path + "/builder-conversation",
            headers=headers,
            json={
                "intent": "edit",
                "expectedRevision": 1,
                "messages": messages,
            },
        )
        assert response.status_code == 409
