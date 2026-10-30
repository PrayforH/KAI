"""新建 Bot 的外观：只影响呈现，随创建请求落进 config，且校验取值。"""

import pytest
from pydantic import ValidationError

from harness.assistant.api import SpecialistAppearance, SpecialistCreateRequest


def test_appearance_defaults_to_platform_accent() -> None:
    appearance = SpecialistAppearance()
    assert appearance.color == "#126fd4"
    assert appearance.avatar == "blob"


def test_create_request_rejects_non_hex_color() -> None:
    with pytest.raises(ValidationError):
        SpecialistAppearance(color="blue")


def test_create_request_carries_appearance_without_identity_fields() -> None:
    body = SpecialistCreateRequest.model_validate(
        {
            "name": "行业快报监控",
            "agentName": "archive-assistant-agent",
            "agentVersion": "0.1.1",
            "appearance": {"color": "#0d9381", "avatar": "cube"},
        }
    )
    dumped = body.model_dump(by_alias=True)
    assert dumped["appearance"] == {"color": "#0d9381", "avatar": "cube"}
    assert "tenantId" not in dumped and "userId" not in dumped


def test_create_accepts_no_agent_binding() -> None:
    """新建 Bot 不强制选执行智能体：请求里可以没有 agentName。"""
    body = SpecialistCreateRequest.model_validate({"name": "演示 Bot", "appearance": {"color": "#ff2d95", "avatar": "star"}})
    dumped = body.model_dump(by_alias=True)
    assert dumped["agentName"] is None and dumped["agentVersion"] is None


def test_profile_request_only_touches_presentation_fields() -> None:
    from harness.assistant.api import SpecialistProfileRequest

    body = SpecialistProfileRequest.model_validate(
        {"name": "Codex 额度重置监控", "title": "研究、营销、管理", "description": "盯重置信号", "expectedRevision": 3}
    )
    dumped = body.model_dump(by_alias=True)
    assert dumped["expectedRevision"] == 3
    assert set(dumped) == {"name", "title", "description", "expectedRevision"}
