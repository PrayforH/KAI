"""失败回放投影：managed overview 需带检查点 id/摘要，供前端一键定位。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant import api


@pytest.mark.asyncio
async def test_managed_overview_projects_checkpoint_context_for_failed_run() -> None:
    checkpoint = SimpleNamespace(
        run_id="run-1",
        checkpoint_id="checkpoint_e1",
        unknown_usage=False,
        tokens=120,
        cost_usd=0.01,
        summary="沙箱启动失败，未执行任何步骤",
    )
    objective = SimpleNamespace(objective_id="obj-1", revision=3, budget=None)
    binding = SimpleNamespace(
        thread_id="thread-1",
        role=SimpleNamespace(value="execute"),
        project_id=None,
        depends_on_objective_id=None,
    )
    repository = SimpleNamespace(
        get=AsyncMock(return_value=objective),
        managed_bindings=AsyncMock(return_value=[binding]),
        checkpoints=AsyncMock(return_value=[checkpoint]),
    )
    assistant = SimpleNamespace(
        repository=repository,
        managed_overview=AsyncMock(
            return_value=[
                {
                    "thread_id": "thread-1",
                    "title": "采购整改任务",
                    "status": "failed",
                    "summary": "整理整改证据",
                    "active": False,
                    "run_id": "run-1",
                    "duration_seconds": 12,
                }
            ]
        ),
        render_managed_overview=lambda overview: "",
    )

    payload = await api.managed_overview(
        "obj-1",
        SimpleNamespace(tenant_id="tenant-a", user_id="user-a"),
        assistant,
    )

    progress = payload["managed"][0]["progress"]
    assert progress["checkpoint_id"] == "checkpoint_e1"
    assert progress["checkpoint_summary"] == "沙箱启动失败，未执行任何步骤"
    assert progress["metrics"] == {
        "tokens": 120,
        "costUsd": 0.01,
        "unknownUsage": False,
    }
