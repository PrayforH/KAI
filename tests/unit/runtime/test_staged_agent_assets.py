from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.core.manifest import load_manifest, materialize_skill_snapshot_set
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    ModelCompatibility,
    ModelRoute,
    Run,
    RunStatus,
    Session,
)
from harness.runtime.base import RuntimeContext
from harness.runtime.claude_sdk import ClaudeSdkRuntime


@pytest.mark.asyncio
async def test_claude_uses_staged_skills_without_replacing_workspace(tmp_path: Path) -> None:
    snapshot = load_manifest("agents/echo-agent/agent.yaml")
    now = datetime.now(UTC)
    version = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="echo-agent",
        version="0.4.1",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=now,
    )
    runtime = ClaudeSdkRuntime(
        agent_version=version,
        routes=[ModelRoute(
            route_id="default",
            provider="new-api",
            base_url="https://new-api.example/v1",
            model="gateway-model",
            compatibility=ModelCompatibility.FULL,
            capabilities=frozenset({"streaming", "tool_use"}),
        )],
        route_secrets={"default": "secret"},
    )
    context = RuntimeContext(
        run=Run(
            run_id="run-1",
            session_id="session-1",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="staged-assets",
            created_at=now,
            updated_at=now,
            input={"prompt": "hello"},
        ),
        session=Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-a",
            agent_name="echo-agent",
            agent_version="0.4.1",
            created_at=now,
        ),
        workspace=tmp_path,
        agent_assets_staged=True,
    )
    materialize_skill_snapshot_set((snapshot,), tmp_path)
    marker = tmp_path / ".claude/skills/workspace-validation/run-created.txt"
    marker.write_text("retained", encoding="utf-8")

    await runtime._options(context, runtime._router.resolve("default").route)

    assert marker.read_text(encoding="utf-8") == "retained"
    assert (tmp_path / ".claude/skills/workspace-validation/SKILL.md").is_file()

    unstaged = context.model_copy(update={"agent_assets_staged": False})
    await runtime._options(unstaged, runtime._router.resolve("default").route)
    assert not marker.exists()
    assert (tmp_path / ".claude/skills/workspace-validation/SKILL.md").is_file()
