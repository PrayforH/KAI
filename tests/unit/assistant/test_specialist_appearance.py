"""新建 Bot 的外观：只影响呈现，随创建请求落进 config，且校验取值。"""

from pathlib import Path

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
    body = SpecialistCreateRequest.model_validate({"name": "演示 Bot",
    "appearance": {"color": "#ff2d95", "avatar": "star"}})
    dumped = body.model_dump(by_alias=True)
    assert dumped["agentName"] is None and dumped["agentVersion"] is None


def test_profile_request_only_touches_presentation_fields() -> None:
    from harness.assistant.api import SpecialistProfileRequest

    body = SpecialistProfileRequest.model_validate(
        {
            "name": "Codex 额度重置监控",
            "title": "研究、营销、管理",
            "description": "盯重置信号",
            "expectedRevision": 3,
        }
    )
    dumped = body.model_dump(by_alias=True)
    assert dumped["expectedRevision"] == 3
    # appearance 现在也是纯呈现字段：编辑弹窗可一并改颜色/形状。
    assert set(dumped) == {
        "name", "title", "description", "appearance", "sidebar", "expectedRevision"
    }


def test_delegate_tool_schema_hides_agent_fields() -> None:
    """派活工具不再向模型暴露 agent_name/agent_version：执行绑定是平台决定。"""
    delegation = Path(__file__).resolve().parents[3] / "src/harness/assistant/delegation.py"
    source = delegation.read_text()
    assert '"agent_name": {"type": "string"}' not in source
    assert "agent_name=None" in source


def test_profile_request_carries_appearance() -> None:
    """编辑弹窗与新建一致：profile 请求可以带颜色/形状。"""
    from harness.assistant.api import SpecialistProfileRequest

    body = SpecialistProfileRequest.model_validate(
        {
            "name": "演示 Bot",
            "appearance": {"color": "#0d9381", "avatar": "star"},
            "expectedRevision": 2,
        }
    )
    dumped = body.model_dump(by_alias=True)
    assert dumped["appearance"] == {"color": "#0d9381", "avatar": "star"}


def test_profile_rejects_bad_appearance_color() -> None:
    import pytest

    from harness.assistant.api import SpecialistProfileRequest

    with pytest.raises(ValidationError):
        SpecialistProfileRequest.model_validate(
            {"appearance": {"color": "blue"}, "expectedRevision": 2}
        )


def test_sidebar_is_presentation_only_and_rejects_execution_fields():
    from harness.assistant.api import SpecialistProfileRequest

    body = SpecialistProfileRequest.model_validate({
        "sidebar": {"pinned": True, "group": "工作", "unread": True}, "expectedRevision": 2,
    })
    assert body.sidebar is not None and body.sidebar.pinned
    assert body.sidebar.group == "工作"
    with pytest.raises(ValidationError):
        SpecialistProfileRequest.model_validate({
            "sidebar": {"status": "active", "schedule_minutes": 15}, "expectedRevision": 2,
        })


@pytest.mark.asyncio
async def test_profile_route_saves_sidebar_without_configuring_or_executing():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from harness.assistant.api import SpecialistProfileRequest, update_specialist_profile

    service = Mock()
    service.update_specialist_profile = AsyncMock(return_value="saved")
    body = SpecialistProfileRequest(sidebar={"pinned": True}, expectedRevision=4)
    result = await update_specialist_profile(
        "bot-1", body, SimpleNamespace(tenant_id="tenant-a", user_id="owner"), service
    )
    assert result == "saved"
    service.update_specialist_profile.assert_awaited_once_with(
        "tenant-a", "owner", "bot-1", name=None, title=None, description=None, appearance=None,
        sidebar={"pinned": True, "hidden": False, "unread": False, "group": ""},
        expected_revision=4,
    )
    service.configure_specialist.assert_not_called()
    service.execute_objective.assert_not_called()
