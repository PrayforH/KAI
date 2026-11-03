"""用户 @某个 Bot 发消息：回执幂等、目标必须可收消息。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.models import (
    HandoverStatus,
    Specialist,
    SpecialistHandover,
    SpecialistStatus,
)
from harness.core.errors import ConflictError, NotFoundError


def _specialist(status: SpecialistStatus = SpecialistStatus.READY) -> Specialist:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    return Specialist(
        specialistId="specialist_a",
        tenantId="t",
        userId="u",
        name="资料 Bot",
        slug="ziliau-bot",
        description="",
        agentName="agent",
        agentVersion="1.0",
        status=status,
        config={},
        createdAt=now,
        updatedAt=now,
    )


def _service(
    specialist: Specialist | None = None,
    handovers: list[SpecialistHandover] | None = None,
):
    from harness.assistant.service import AssistantService

    repo, agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(7)]
    if specialist is None:
        repo.get_specialist.side_effect = NotFoundError("missing")
    else:
        repo.get_specialist.return_value = specialist
    repo.handovers.return_value = handovers or []
    repo.record_handover.side_effect = lambda value: value
    auth.profile.return_value = (SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
    agui.get_binding.return_value = SimpleNamespace(session_id="session")
    service = AssistantService(
        repo, agents=agents, auth=auth, projects=projects, agui=agui, runs=runs, events=events
    )
    return service, repo


@pytest.mark.asyncio
async def test_mention_records_a_durable_handover() -> None:
    service, repo = _service(_specialist())
    handover = await service.record_mention_handover(
        "t", "u", "specialist_a", text="帮我看看这份资料", idempotency_key="key-12345678"
    )
    assert handover.specialist_id == "specialist_a"
    assert handover.message_id == "mention-specialist:key-12345678:specialist_a"
    assert handover.status is HandoverStatus.SENT
    assert handover.summary == "帮我看看这份资料"
    repo.record_handover.assert_awaited_once()


@pytest.mark.asyncio
async def test_mention_with_the_same_key_never_duplicates_the_receipt() -> None:
    existing = SpecialistHandover(
        handoverId="handover_old",
        tenantId="t",
        userId="u",
        specialistId="specialist_a",
        messageId="mention-specialist:key-12345678:specialist_a",
        summary="已经发过了",
        status=HandoverStatus.SENT,
        createdAt=datetime(2026, 11, 3, tzinfo=UTC),
        updatedAt=datetime(2026, 11, 3, tzinfo=UTC),
    )
    service, repo = _service(_specialist(), [existing])
    handover = await service.record_mention_handover(
        "t", "u", "specialist_a", text="帮我看看这份资料", idempotency_key="key-12345678"
    )
    assert handover.handover_id == "handover_old"
    repo.record_handover.assert_not_called()


@pytest.mark.asyncio
async def test_mention_to_an_archived_bot_is_refused() -> None:
    """删除/停用的 Bot 不再接收新消息：回执也不该留下。"""
    service, repo = _service()
    repo.get_specialist.side_effect = None
    repo.get_specialist.return_value = _specialist(SpecialistStatus.ARCHIVED)
    with pytest.raises(ConflictError):
        await service.record_mention_handover(
            "t", "u", "specialist_a", text="在吗", idempotency_key="key-12345678"
        )
    repo.record_handover.assert_not_called()


@pytest.mark.asyncio
async def test_mention_to_a_paused_bot_is_refused() -> None:
    """停用（暂停接单）的 Bot 也不接收新消息。"""
    service, repo = _service()
    repo.get_specialist.side_effect = None
    repo.get_specialist.return_value = _specialist(SpecialistStatus.PAUSED)
    with pytest.raises(ConflictError):
        await service.record_mention_handover(
            "t", "u", "specialist_a", text="在吗", idempotency_key="key-12345678"
        )
    repo.record_handover.assert_not_called()


@pytest.mark.asyncio
async def test_mention_to_another_users_bot_is_not_found() -> None:
    """跨用户 ID：别人的 Bot 既不能投递，也不能留下回执。"""
    service, repo = _service()
    repo.get_specialist.side_effect = NotFoundError("missing")
    with pytest.raises(NotFoundError):
        await service.record_mention_handover(
            "t", "u", "specialist_other_user", text="在吗", idempotency_key="key-12345678"
        )
    repo.record_handover.assert_not_called()


def test_routine_executions_carry_the_assistant_scope() -> None:
    """例行执行（每天那条提醒）必须带助理范围，否则退化成没有联网检索能力。

    早先每条天气提醒都答「本运行没有联网检索能力」，就是这条标记漏在聊天入口里。
    """
    from harness.assistant.service import routine_forwarded_props

    assert routine_forwarded_props() == {"assistantScope": True}
