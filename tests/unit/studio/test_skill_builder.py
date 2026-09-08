import json

import httpx
import pytest
from pydantic import SecretStr

from harness.runtime.cc_switch import CcSwitchClaudeConfig
from harness.studio.skill_builder import (
    AnthropicCompatibleSkillConversationService,
    SkillConversationRequest,
    SkillConversationUnavailableError,
    SkillConversationUpstreamError,
)


def request(model_route: str = "new-api-default") -> SkillConversationRequest:
    return SkillConversationRequest.model_validate(
        {
            "modelRoute": model_route,
            "context": {
                "agentName": "contract-reviewer",
                "displayName": "合同审查",
                "domain": "contract-review",
                "description": "审查合同并输出风险。",
                "currentSkill": {
                    "name": "review-contracts",
                    "description": "审查合同。",
                    "instructions": "# 工作流\n\n读取合同并检查风险。",
                    "files": [],
                },
            },
            "messages": [
                {
                    "role": "user",
                    "content": "增加付款、违约和续约条款检查，并输出风险表。",
                }
            ],
        }
    )


def gateway(route_id: str | None = "new-api-default") -> CcSwitchClaudeConfig:
    return CcSwitchClaudeConfig(
        route_id=route_id,
        base_url="https://model.example.test",
        model="claude-sonnet",
        provider="new-api",
        credential=SecretStr("model-secret"),
        auth_scheme="bearer",
    )


@pytest.mark.asyncio
async def test_skill_conversation_returns_a_valid_reviewable_draft() -> None:
    captured: dict[str, object] = {}

    async def handler(http_request: httpx.Request) -> httpx.Response:
        captured["authorization"] = http_request.headers["authorization"]
        captured["body"] = json.loads(http_request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "status": "ready",
                                "reply": "已整理为可审阅草稿。",
                                "skill": {
                                    "name": "review-contract-risks",
                                    "description": "审查合同付款、违约和续约条款时使用。",
                                    "instructions": (
                                        "# 工作流\n\n"
                                        "1. 读取合同。\n"
                                        "2. 提取付款、违约和续约条款。\n"
                                        "3. 输出带原文位置的风险表。"
                                    ),
                                    "files": [
                                        {
                                            "path": "references/risk-levels.md",
                                            "content": "# 风险等级\n\n按影响和发生概率分级。",
                                        }
                                    ],
                                },
                                "followUpQuestions": [],
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = AnthropicCompatibleSkillConversationService(
            (gateway(),),
            http_client=client,
        )
        reply = await service.respond("tenant-a", request())

    assert reply.status == "ready"
    assert reply.skill is not None
    assert reply.skill.name == "review-contract-risks"
    assert reply.skill.files[0].path == "references/risk-levels.md"
    assert captured["authorization"] == "Bearer model-secret"
    body = captured["body"]
    assert isinstance(body, dict)
    assert "model-secret" not in json.dumps(body)
    assert body["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_skill_conversation_rejects_an_unconfigured_route_before_calling_model() -> None:
    service = AnthropicCompatibleSkillConversationService((gateway("anthropic-official"),))

    with pytest.raises(
        SkillConversationUnavailableError,
        match="new-api-default",
    ):
        await service.respond("tenant-a", request())


@pytest.mark.asyncio
async def test_skill_conversation_rejects_invalid_model_json() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "not a Skill draft"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = AnthropicCompatibleSkillConversationService(
            (gateway(None),),
            http_client=client,
        )
        with pytest.raises(SkillConversationUpstreamError, match="JSON"):
            await service.respond("tenant-a", request("tenant-route"))
