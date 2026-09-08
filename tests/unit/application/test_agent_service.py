from datetime import UTC, datetime
from pathlib import Path
from shutil import copytree

import pytest

from harness.adapters.memory import InMemoryAgentRegistry, InMemorySessionRepository
from harness.agent_package import AgentPackageCheckError, pack_agent_package
from harness.application.agents import AgentService
from harness.application.sessions import SessionService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.manifest import load_manifest
from harness.core.models import AgentVersion, AgentVersionStatus

FIXTURE = Path("tests/fixtures/agents/echo-agent/agent.yaml")
NOW = datetime(2026, 7, 11, tzinfo=UTC)


@pytest.mark.asyncio
async def test_publishes_an_immutable_manifest_snapshot() -> None:
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW)

    version = await service.publish("tenant-a", "user-1", FIXTURE)

    assert version.status is AgentVersionStatus.PUBLISHED
    assert version.manifest_hash == version.snapshot["content_hash"]
    assert (await registry.get("tenant-a", "user-1", "echo-agent", "0.1.0")) == version


@pytest.mark.asyncio
async def test_preview_snapshot_preserves_stable_agent_identity() -> None:
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW)
    snapshot = load_manifest(FIXTURE)

    version = await service.register_preview_snapshot(
        "tenant-a",
        "user-1",
        snapshot,
        version="preview-draft-1",
        agent_id="agent-1",
    )

    assert version.status is AgentVersionStatus.VALIDATED
    assert version.agent_id == "agent-1"
    assert (
        await registry.get("tenant-a", "user-1", "echo-agent", "preview-draft-1")
    ).agent_id == "agent-1"


@pytest.mark.asyncio
async def test_production_service_enforces_full_package_gate() -> None:
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW, environment="production")

    with pytest.raises(AgentPackageCheckError, match="production package check"):
        await service.publish("tenant-a", "user-1", FIXTURE)


@pytest.mark.asyncio
async def test_production_service_publishes_ready_reference_agent() -> None:
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW, environment="production")

    version = await service.publish(
        "tenant-a", "user-1", Path("agents/public-opinion-agent/agent.yaml")
    )

    assert version.name == "public-opinion-agent"
    assert version.snapshot["skill_snapshots"]


@pytest.mark.asyncio
async def test_service_publishes_reproducible_bundle(tmp_path: Path) -> None:
    archive, _ = pack_agent_package(
        "agents/public-opinion-agent/agent.yaml", output_directory=tmp_path
    )
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW, environment="production")

    version = await service.publish_bundle("tenant-a", "user-1", archive.read_bytes())

    assert version.name == "public-opinion-agent"
    assert version.manifest_hash == version.snapshot["content_hash"]
    assert version.package_hash is not None


@pytest.mark.asyncio
async def test_exact_release_retry_is_idempotent() -> None:
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW)

    first = await service.publish("tenant-a", "user-1", FIXTURE)
    repeated = await service.publish("tenant-a", "user-1", FIXTURE)

    assert repeated == first


@pytest.mark.asyncio
async def test_provisions_an_isolated_default_for_each_user() -> None:
    registry = InMemoryAgentRegistry()
    service = AgentService(
        registry,
        clock=lambda: NOW,
        environment="production",
        default_manifest_path="agents/lead-agent/agent.yaml",
    )

    first = await service.ensure_user_default("tenant-a", "user-1")
    repeated = await service.ensure_user_default("tenant-a", "user-1")
    second_user = await service.ensure_user_default("tenant-a", "user-2")

    assert first is not None
    assert first.name == "lead-agent"
    assert first.package_hash is not None
    assert repeated == first
    assert second_user is not None
    assert second_user.owner_user_id == "user-2"
    assert await registry.list_for_user("tenant-a", "user-1") == [first]
    assert await registry.list_for_user("tenant-a", "user-2") == [second_user]


@pytest.mark.asyncio
async def test_same_release_identity_rejects_different_content(tmp_path: Path) -> None:
    package = tmp_path / "echo-agent"
    copytree(FIXTURE.parent, package)
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW)
    await service.publish("tenant-a", "user-1", package / "agent.yaml")
    prompt = package / "prompts/system.md"
    prompt.write_text(prompt.read_text() + "\nChanged without a version bump.\n")

    with pytest.raises(ConflictError, match="already exists"):
        await service.publish("tenant-a", "user-1", package / "agent.yaml")


@pytest.mark.asyncio
async def test_bundle_retry_rejects_changed_evaluation_without_version_bump(
    tmp_path: Path,
) -> None:
    package = tmp_path / "public-opinion-agent"
    copytree(Path("agents/public-opinion-agent"), package)
    first_archive, _ = pack_agent_package(
        package / "agent.yaml", output_directory=tmp_path / "first"
    )
    registry = InMemoryAgentRegistry()
    service = AgentService(registry, clock=lambda: NOW, environment="production")
    await service.publish_bundle("tenant-a", "user-1", first_archive.read_bytes())
    suite = package / "evals/suite.yaml"
    suite.write_text(suite.read_text() + "\n# Changed release evidence.\n")
    second_archive, _ = pack_agent_package(
        package / "agent.yaml", output_directory=tmp_path / "second"
    )

    with pytest.raises(ConflictError, match="already exists"):
        await service.publish_bundle("tenant-a", "user-1", second_archive.read_bytes())


@pytest.mark.asyncio
async def test_session_requires_a_published_agent_version() -> None:
    registry = InMemoryAgentRegistry()
    sessions = InMemorySessionRepository()
    await registry.add(
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-1",
            name="echo-agent",
            version="0.1.0",
            status=AgentVersionStatus.VALIDATED,
            manifest_hash="a" * 64,
            created_at=NOW,
        )
    )
    service = SessionService(
        registry,
        sessions,
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-1",
        require_published_dependencies=True,
    )

    with pytest.raises(ConflictError, match="published"):
        await service.create("tenant-a", "user-1", "echo-agent", "0.1.0")


@pytest.mark.asyncio
async def test_preview_session_allows_validated_root_with_published_dependencies() -> None:
    registry = InMemoryAgentRegistry()
    sessions = InMemorySessionRepository()
    root = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    child = load_manifest("tests/fixtures/agents/helper-agent/agent.yaml")
    for snapshot, status in (
        (root, AgentVersionStatus.VALIDATED),
        (child, AgentVersionStatus.VALIDATED),
    ):
        metadata = snapshot.manifest.metadata
        await registry.add(
            AgentVersion(
                tenant_id="tenant-a",
                owner_user_id="user-1",
                name=metadata.name,
                version=metadata.version,
                status=status,
                manifest_hash=snapshot.content_hash,
                snapshot=snapshot.model_dump(mode="json"),
                created_at=NOW,
            )
        )
    service = SessionService(
        registry,
        sessions,
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-1",
        require_published_dependencies=True,
    )

    session = await service.create(
        "tenant-a",
        "user-1",
        "echo-agent",
        "0.1.0",
        preview=True,
    )

    assert session.environment == "preview"


@pytest.mark.asyncio
async def test_session_fails_early_when_pinned_subagent_is_missing() -> None:
    registry = InMemoryAgentRegistry()
    sessions = InMemorySessionRepository()
    parent = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    await registry.add(
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-1",
            name="echo-agent",
            version=parent.manifest.metadata.version,
            status=AgentVersionStatus.PUBLISHED,
            manifest_hash=parent.content_hash,
            snapshot=parent.model_dump(mode="json"),
            created_at=NOW,
        )
    )
    service = SessionService(
        registry,
        sessions,
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-1",
        require_published_dependencies=True,
    )

    with pytest.raises(NotFoundError, match="helper@1.0.0"):
        await service.create(
            "tenant-a", "user-1", "echo-agent", parent.manifest.metadata.version
        )


@pytest.mark.asyncio
async def test_session_fails_early_when_pinned_subagent_is_not_published() -> None:
    registry = InMemoryAgentRegistry()
    sessions = InMemorySessionRepository()
    parent = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    child = load_manifest("tests/fixtures/agents/helper-agent/agent.yaml")
    for version in (
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-1",
            name="echo-agent",
            version=parent.manifest.metadata.version,
            status=AgentVersionStatus.PUBLISHED,
            manifest_hash=parent.content_hash,
            snapshot=parent.model_dump(mode="json"),
            created_at=NOW,
        ),
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-1",
            name="helper",
            version="1.0.0",
            status=AgentVersionStatus.VALIDATED,
            manifest_hash=child.content_hash,
            snapshot=child.model_dump(mode="json"),
            created_at=NOW,
        ),
    ):
        await registry.add(version)
    service = SessionService(
        registry,
        sessions,
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-1",
        require_published_dependencies=True,
    )

    with pytest.raises(ConflictError, match="subagent must be published"):
        await service.create(
            "tenant-a", "user-1", "echo-agent", parent.manifest.metadata.version
        )
