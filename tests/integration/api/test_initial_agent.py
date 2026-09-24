import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from harness.studio.api import get_model_configuration_service
from tests.integration.api.test_agent_studio_api import (
    INITIAL_AGENT_RESPONSE,
    SERVICE_TOKEN,
    app,
)


@pytest.mark.asyncio
async def test_model_initial_draft_recommends_then_reviews_pinned_skills() -> None:
    application = app()
    model = AsyncMock()
    response = json.loads(INITIAL_AGENT_RESPONSE)
    response["recommendedSkills"] = [
        {"packageId": "evidence-reporting", "reason": "报告需要引用核验"}
    ]
    model.complete_text.return_value = json.dumps(response)
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/studio/drafts/from-task",
            headers=headers,
            json={"task": "分析公开资料，联网核验后撰写报告"},
        )
        assert result.status_code == 201, result.text
        model.complete_text.assert_awaited_once()
        sent = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
        assert sent["request"]["task"] == "分析公开资料，联网核验后撰写报告"
        assert "apiKey" not in str(sent)
        data = result.json()
        assert data["draft"]["spec"]["systemPrompt"] == response["systemPrompt"]
        assert data["draft"]["spec"]["skills"] == []
        recommendation = data["recommendation"]
        assert recommendation["generatedByModel"] is True
        skill = recommendation["recommendedSkills"][0]
        path = "/v1/studio/drafts/" + data["draft"]["draftId"]
        changes = {
            "installSkills": [{"packageId": skill["packageId"], "revision": skill["revision"]}],
            "capabilityCatalogRevision": recommendation["capabilityCatalogRevision"],
        }
        body = {"expectedRevision": 1, "changes": changes}
        preview = await client.post(path + "/builder-project-diff", headers=headers, json=body)
        assert preview.status_code == 200, preview.text
        assert (await client.get(path, headers=headers)).json()["revision"] == 1
        applied = await client.post(path + "/builder-apply", headers=headers, json=body)
        assert applied.status_code == 200, applied.text
        installed = applied.json()["spec"]["skills"][0]
        assert installed["source"]["packageId"] == "evidence-reporting"
        assert installed["source"]["modified"] is False
        assert (
            await client.post(path + "/builder-apply", headers=headers, json=body)
        ).status_code == 409
        # Invalid model recommendations fail without saving another draft.
        response["recommendedSkills"][0]["packageId"] = "made-up-skill"
        model.complete_text.return_value = json.dumps(response)
        invalid = await client.post(
            "/v1/studio/drafts/from-task", headers=headers, json={"task": "创建另一个研究助手"}
        )
        assert invalid.status_code == 409
        model.complete_text.return_value = "not JSON"
        invalid = await client.post(
            "/v1/studio/drafts/from-task", headers=headers, json={"task": "创建另一个研究助手"}
        )
        assert invalid.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [["Read", "WebSearch", "WebFetch"], ["Read"]])
async def test_initial_draft_persists_model_selected_tools(tools: list[str]) -> None:
    application = app()
    model = AsyncMock()
    response = {**json.loads(INITIAL_AGENT_RESPONSE), "builtinTools": tools}
    model.complete_text.return_value = json.dumps(response)
    application.dependency_overrides[get_model_configuration_service] = lambda: model
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        result = await client.post(
            "/v1/studio/drafts/from-task",
            headers=headers,
            json={
                "task": "创建研究助手，开启联网检索"
                if "WebSearch" in tools
                else "创建助手，仅离线读取材料"
            },
        )
        assert result.status_code == 201, result.text
        data = result.json()
        assert data["draft"]["spec"]["builtinTools"] == tools
        assert data["recommendation"]["builtinTools"] == tools
        sent = json.loads(model.complete_text.call_args.kwargs["user_prompt"])
        assert "WebSearch" in {t["name"] for t in sent["availableTools"]}
        saved = await client.get("/v1/studio/drafts/" + data["draft"]["draftId"], headers=headers)
        assert saved.json()["spec"]["builtinTools"] == tools
        response["builtinTools"] = ["invented-network-tool"]
        model.complete_text.return_value = json.dumps(response)
        invalid = await client.post(
            "/v1/studio/drafts/from-task", headers=headers, json={"task": "创建另一个助手"}
        )
        assert invalid.status_code == 409
