"""Bot 外观：服务端是调色板的唯一权威，新建时从未用过的外观里随机取。"""

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.appearance import (
    APPEARANCE_COLORS,
    APPEARANCE_SHAPES,
    choose_appearance,
)

CLIENT_APPEARANCE = (
    Path(__file__).resolve().parents[3]
    / "web/harness-console/src/lib/bot-appearance.ts"
)


def test_client_and_server_share_one_palette() -> None:
    """前端只留 SVG 路径；颜色表与形状 id 由服务端给出，两边不能各写一份。"""
    source = CLIENT_APPEARANCE.read_text()
    assert "BOT_COLORS" not in source
    shape_ids = re.findall(r'\{ id: "([a-z0-9-]+)", path:', source)
    assert shape_ids == list(APPEARANCE_SHAPES)


def test_choose_prefers_an_unused_color_and_shape() -> None:
    picked = choose_appearance([{"color": "#ffffff", "avatar": "blob"}])
    assert picked["color"] != "#ffffff"
    assert picked["avatar"] != "blob"
    assert picked["color"] in APPEARANCE_COLORS
    assert picked["avatar"] in APPEARANCE_SHAPES


def test_choose_prefers_an_unused_combination_when_colors_run_out() -> None:
    """颜色都用过了，但组合还没用完：先走「未用组合」，不立刻重复。"""
    used = [{"color": color, "avatar": "blob"} for color in APPEARANCE_COLORS]
    picked = choose_appearance(used)
    assert picked["avatar"] != "blob"


def test_choose_falls_back_to_the_least_repeated() -> None:
    """全用完以后退到出现次数最少的那套，而不是永远挑第一个。"""
    used = [
        {"color": color, "avatar": shape}
        for color in APPEARANCE_COLORS
        for shape in APPEARANCE_SHAPES
    ]
    picked = choose_appearance(used)
    assert picked["color"] in APPEARANCE_COLORS
    assert picked["avatar"] in APPEARANCE_SHAPES


def _service(siblings: list):
    from harness.assistant.service import AssistantService

    repo, agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(7)]
    repo.list_specialists.return_value = siblings
    repo.create_specialist.side_effect = lambda value: value
    auth.profile.return_value = (SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
    agui.get_binding.return_value = SimpleNamespace(session_id="session")
    service = AssistantService(
        repo, agents=agents, auth=auth, projects=projects, agui=agui, runs=runs, events=events
    )
    service._default_agent = AsyncMock(return_value=("agent", "1.0"))  # noqa: SLF001
    return service, repo


def _sibling(slug: str, color: str, avatar: str):
    from harness.assistant.models import Specialist, SpecialistStatus

    now = datetime(2026, 11, 3, tzinfo=UTC)
    return Specialist(
        specialistId=f"specialist_{slug}",
        tenantId="t",
        userId="u",
        name=slug,
        slug=slug,
        description="",
        agentName="agent",
        agentVersion="1.0",
        status=SpecialistStatus.READY,
        config={"appearance": {"color": color, "avatar": avatar}},
        createdAt=now,
        updatedAt=now,
    )


@pytest.mark.asyncio
async def test_create_assigns_a_random_appearance_and_persists_it() -> None:
    service, repo = _service([_sibling("taken", "#ffffff", "blob")])
    created, fresh = await service.create_specialist("t", "u", name="新 Bot", description="")
    assert fresh is True
    appearance = created.config["appearance"]
    assert appearance["color"] != "#ffffff"
    assert appearance["avatar"] != "blob"
    # 写进资料里：刷新与重读都拿到同一份，不会重摇。
    assert repo.create_specialist.await_count == 1


@pytest.mark.asyncio
async def test_create_keeps_a_manually_chosen_appearance() -> None:
    service, _ = _service([])
    created, _ = await service.create_specialist(
        "t", "u", name="手选 Bot", description="",
        appearance={"color": "#0d9381", "avatar": "leaf"},
    )
    assert created.config["appearance"] == {"color": "#0d9381", "avatar": "leaf"}


@pytest.mark.asyncio
async def test_create_reuses_an_existing_bot_without_touching_its_appearance() -> None:
    """同名重试（幂等）：直接复用既有实例，外观不重置。"""
    from harness.assistant.service import _specialist_slug

    existing = _sibling(_specialist_slug("Same Name"), "#7442d1", "star")
    service, repo = _service([existing])
    created, fresh = await service.create_specialist("t", "u", name="Same Name", description="")
    assert fresh is False
    assert created.config["appearance"] == {"color": "#7442d1", "avatar": "star"}
    repo.create_specialist.assert_not_called()


def test_palette_endpoint_lists_the_same_catalogue() -> None:
    from harness.assistant.service import AssistantService

    palette = AssistantService.appearance_palette(AssistantService.__new__(AssistantService))
    assert palette["colors"] == list(APPEARANCE_COLORS)
    assert palette["avatars"] == list(APPEARANCE_SHAPES)
