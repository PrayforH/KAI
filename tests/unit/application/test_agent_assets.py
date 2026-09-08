from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.adapters.memory import InMemoryAgentRegistry
from harness.application.agent_assets import (
    resolve_published_agent_versions,
    stage_published_agent_assets,
)
from harness.core.manifest import (
    ManifestValidationError,
    SubagentSpec,
    load_manifest,
)
from harness.core.models import AgentVersion, AgentVersionStatus


async def _publish(registry: InMemoryAgentRegistry, manifest_path: str) -> None:
    snapshot = load_manifest(manifest_path)
    metadata = snapshot.manifest.metadata
    await registry.add(
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-1",
            name=metadata.name,
            version=metadata.version,
            status=AgentVersionStatus.PUBLISHED,
            manifest_hash=snapshot.content_hash,
            snapshot=snapshot.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )
    )


@pytest.mark.asyncio
async def test_stages_main_and_pinned_subagent_skills(tmp_path: Path) -> None:
    registry = InMemoryAgentRegistry()
    await _publish(registry, "agents/helper-agent/agent.yaml")
    await _publish(registry, "agents/echo-agent/agent.yaml")

    names = await stage_published_agent_assets(
        registry,
        tenant_id="tenant-a",
        owner_user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.4.1",
        workspace=tmp_path,
    )

    assert names == ("delegated-investigation", "workspace-validation")
    assert (tmp_path / ".claude/skills/delegated-investigation/SKILL.md").is_file()
    assert (tmp_path / ".claude/skills/workspace-validation/SKILL.md").is_file()
    assert not (tmp_path / ".agents/skills").exists()


@pytest.mark.asyncio
async def test_stages_pinned_roles_as_codex_project_agents(tmp_path: Path) -> None:
    registry = InMemoryAgentRegistry()
    child = load_manifest("agents/helper-agent/agent.yaml")
    root = load_manifest("agents/echo-agent/agent.yaml")
    root = root.model_copy(
        update={
            "manifest": root.manifest.model_copy(
                update={
                    "spec": root.manifest.spec.model_copy(update={"runtime": "codex-app-server"})
                }
            )
        }
    )
    now = datetime.now(UTC)
    for snapshot in (child, root):
        metadata = snapshot.manifest.metadata
        await registry.add(
            AgentVersion(
                tenant_id="tenant-a",
                owner_user_id="user-1",
                name=metadata.name,
                version=metadata.version,
                status=AgentVersionStatus.PUBLISHED,
                manifest_hash=snapshot.content_hash,
                snapshot=snapshot.model_dump(mode="json"),
                created_at=now,
            )
        )

    await stage_published_agent_assets(
        registry,
        tenant_id="tenant-a",
        owner_user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.4.1",
        workspace=tmp_path,
    )

    config = (tmp_path / ".codex/agents/harness-helper-agent.toml").read_text()
    assert 'name = "helper-agent"' in config
    assert 'sandbox_mode = "read-only"' in config
    assert "Delegated investigation helper" in config
    assert "privateToken" not in config
    assert (
        tmp_path / ".agents/skills/harness-delegated-investigation/SKILL.md"
    ).is_file()
    assert (tmp_path / ".agents/skills/harness-workspace-validation/SKILL.md").is_file()


@pytest.mark.asyncio
async def test_codex_skill_mirror_preserves_user_authored_skills(tmp_path: Path) -> None:
    registry = InMemoryAgentRegistry()
    await _publish(registry, "agents/helper-agent/agent.yaml")
    root = load_manifest("agents/echo-agent/agent.yaml")
    root = root.model_copy(
        update={
            "manifest": root.manifest.model_copy(
                update={
                    "spec": root.manifest.spec.model_copy(update={"runtime": "codex-app-server"})
                }
            )
        }
    )
    metadata = root.manifest.metadata
    await registry.add(
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-1",
            name=metadata.name,
            version=metadata.version,
            status=AgentVersionStatus.PUBLISHED,
            manifest_hash=root.content_hash,
            snapshot=root.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )
    )
    user_skill = tmp_path / ".agents/skills/user-authored/SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("user content", encoding="utf-8")

    await stage_published_agent_assets(
        registry,
        tenant_id="tenant-a",
        owner_user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.4.1",
        workspace=tmp_path,
    )

    assert user_skill.read_text(encoding="utf-8") == "user content"


@pytest.mark.asyncio
async def test_pinned_subagent_rejects_nested_delegation() -> None:
    registry = InMemoryAgentRegistry()
    root = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    child = load_manifest("tests/fixtures/agents/helper-agent/agent.yaml")
    child_spec = child.manifest.spec.model_copy(
        update={"subagents": (SubagentSpec(ref="leaf@1.0.0"),)}
    )
    child = child.model_copy(
        update={"manifest": child.manifest.model_copy(update={"spec": child_spec})}
    )
    now = datetime.now(UTC)
    for snapshot in (child, root):
        metadata = snapshot.manifest.metadata
        await registry.add(
            AgentVersion(
                tenant_id="tenant-a",
                owner_user_id="user-1",
                name=metadata.name,
                version=metadata.version,
                status=AgentVersionStatus.PUBLISHED,
                manifest_hash=snapshot.content_hash,
                snapshot=snapshot.model_dump(mode="json"),
                created_at=now,
            )
        )

    with pytest.raises(ManifestValidationError, match="nested"):
        await resolve_published_agent_versions(
            registry,
            tenant_id="tenant-a",
            owner_user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
        )
