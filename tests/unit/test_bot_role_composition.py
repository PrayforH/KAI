"""Only the resolved Bot role changes acknowledgement behavior, never client props."""

# pyright: reportPrivateUsage=false

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.composition import build_production_container
from harness.core.models import Run, RunStatus, Session
from harness.runtime.platform_tools import PlatformToolOverlay
from tests.unit.test_production_composition import production_settings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,surface,project",
    [
        ("coordinator", "task", None),
        ("specialist", "assistant", None),
        ("ordinary", "assistant", None),
        ("coordinator", "assistant", None),
        ("coordinator", "assistant", "project-a"),
    ],
)
async def test_resolved_bot_role_survives_overlay_composition(monkeypatch, role, surface, project):
    profile = SimpleNamespace(name="编码协同", description="检查代码", slug="coding")
    coordinator = role == "coordinator"
    monkeypatch.setattr(
        "harness.composition.coordinator_session", AsyncMock(return_value=coordinator)
    )
    monkeypatch.setattr(
        "harness.composition.session_specialist",
        AsyncMock(return_value=profile if role == "specialist" else None),
    )
    monkeypatch.setattr(
        "harness.composition.objective_observer_session", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "harness.studio.runtime_builder.builder_overlay",
        AsyncMock(return_value=PlatformToolOverlay()),
    )
    workflow = SimpleNamespace(before_tool=lambda _: None, before_finish=lambda: None)
    routing = AsyncMock(return_value=PlatformToolOverlay(workflow=workflow))
    monkeypatch.setattr("harness.composition.assistant_routing_overlay", routing)
    container = build_production_container(production_settings())
    monkeypatch.setattr(container.assistant, "primary_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(
        container.assistant.repository, "list_specialists", AsyncMock(return_value=[])
    )
    now = datetime.now(UTC)
    try:
        overlay = await container.worker._platform_tools_factory(
            Run(
                run_id="role-run",
                session_id="role-session",
                tenant_id="tenant-a",
                status=RunStatus.RUNNING,
                idempotency_key="role",
                created_at=now,
                updated_at=now,
                input={
                    "prompt": "检查代码",
                    "require_public_preface": False,
                    "conversation_surface": surface,
                    "business_project_id": project,
                },
            ),
            Session(
                session_id="role-session",
                tenant_id="tenant-a",
                user_id="user-a",
                agent_name="echo-agent",
                agent_version="1",
                created_at=now,
            ),
            None,
            None,
        )
        routed = coordinator and surface == "assistant" and project is None
        assert (overlay.workflow is workflow) is routed
        assert routing.await_count == int(routed)
        assert overlay.require_public_preface is (role != "specialist")
        assert ("直接开始执行，不先复述任务" in overlay.instructions) is (role == "specialist")
        if role == "specialist":
            assert "assistant_delegate_to_specialist" not in {tool.name for tool in overlay.tools}
    finally:
        await container.close()
