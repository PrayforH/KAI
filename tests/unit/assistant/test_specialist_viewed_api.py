"""查看口径：GET 详情纯读；POST /specialists/{id}/viewed 才刷新查看并清暂停。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from harness.api.dependencies import Identity
from harness.assistant import api as assistant_api
from harness.assistant.models import Specialist, SpecialistStatus
from harness.core.errors import NotFoundError

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
IDENTITY = Identity(tenant_id="tenant-a", user_id="user-a")


def _specialist(**overrides: Any) -> Specialist:
    payload: dict[str, Any] = {
        "specialistId": "specialist_pause",
        "tenantId": "tenant-a",
        "userId": "user-a",
        "name": "无人查看验收",
        "slug": "unviewed-check",
        "description": "例行程序闸门",
        "agentName": "audit-agent",
        "agentVersion": "1.0.0",
        "status": SpecialistStatus.ACTIVE,
        "revision": 3,
        "config": {"schedule_minutes": 1440, "notify_channel": "inbox"},
        "lastViewedAt": "2026-10-29T12:00:00Z",
        "routinePausedReason": {
            "reason": "unviewed",
            "hours_since_view": 48,
            "at": "2026-10-31T11:00:00Z",
        },
        "createdAt": "2026-10-20T00:00:00Z",
        "updatedAt": "2026-10-31T11:00:00Z",
    }
    payload.update(overrides)
    return Specialist.model_validate(payload)


class _Repo:
    def __init__(self, specialist: Specialist) -> None:
        self.specialist = specialist

    async def get_specialist(
        self, tenant_id: str, user_id: str, specialist_id: str
    ) -> Specialist:
        return self.specialist

    async def list(self, tenant_id: str, user_id: str) -> list[Any]:
        return []

    async def managed_bindings(
        self, tenant_id: str, user_id: str, *, objective_id: str
    ) -> list[Any]:
        return []


class _Service:
    """只实现这两条路由用到的接口；touch 被调用即记录。"""

    def __init__(self, specialist: Specialist, *, found: bool = True) -> None:
        self.repository = _Repo(specialist)
        self.found = found
        self.touches = 0

    async def touch_specialist_viewed(
        self, tenant_id: str, user_id: str, specialist_id: str
    ) -> bool:
        self.touches += 1
        if not self.found:
            return False
        self.repository.specialist = self.repository.specialist.model_copy(
            update={
                "last_viewed_at": NOW,
                "routine_paused_reason": None,
                "updated_at": NOW,
            }
        )
        return True


@pytest.mark.asyncio
async def test_detail_read_is_pure_and_keeps_the_pause_marker() -> None:
    """打开详情/编辑弹窗不是查看：不能顺带清掉 routinePausedReason。"""
    service = _Service(_specialist())
    detail = await assistant_api.get_specialist("specialist_pause", IDENTITY, service)  # type: ignore[arg-type]

    assert service.touches == 0
    specialist = detail["specialist"]
    assert specialist.routine_paused_reason is not None
    assert specialist.routine_paused_reason["reason"] == "unviewed"
    assert specialist.last_viewed_at == datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_viewed_endpoint_refreshes_last_viewed_and_clears_pause() -> None:
    """进入会话的显式查看：刷新 lastViewedAt、清暂停标记，revision 不变。"""
    service = _Service(_specialist())
    viewed = await assistant_api.mark_specialist_viewed("specialist_pause", IDENTITY, service)  # type: ignore[arg-type]

    assert service.touches == 1
    assert viewed.routine_paused_reason is None
    assert viewed.last_viewed_at is not None
    assert viewed.last_viewed_at.replace(microsecond=0) == NOW
    assert viewed.revision == 3  # 查看不参与配置 CAS


@pytest.mark.asyncio
async def test_viewed_endpoint_reports_missing_specialist() -> None:
    service = _Service(_specialist(), found=False)
    with pytest.raises(NotFoundError):
        await assistant_api.mark_specialist_viewed("specialist_missing", IDENTITY, service)  # type: ignore[arg-type]
