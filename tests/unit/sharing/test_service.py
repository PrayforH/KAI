# pyright: reportPrivateUsage=false

from datetime import UTC, datetime

import pytest

from harness.adapters.memory import InMemoryAgentRegistry
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.evals.suite import EvalCase
from harness.sharing.models import (
    AgentPermission,
    AgentScope,
    GranteeType,
    SpaceRole,
    WorkspaceAgent,
)
from harness.sharing.repositories import InMemoryTeamSpaceRepository
from harness.sharing.service import TeamSpaceService
from harness.sharing.workspace_repositories import InMemoryWorkspaceAgentRepository
from harness.studio.models import (
    AgentDraftSpec,
    AgentTemplate,
    DraftModelSelection,
    DraftSkill,
)


def agent(owner: str, version: str = "1.0.0", name: str = "research-agent") -> AgentVersion:
    return AgentVersion(
        tenant_id="tenant-a",
        owner_user_id=owner,
        name=name,
        version=version,
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=f"hash-{owner}-{version}",
        snapshot={"manifest": {"metadata": {"name": name}}},
        created_at=datetime(2026, 8, 3, tzinfo=UTC),
    )


def service() -> tuple[TeamSpaceService, InMemoryAgentRegistry, InMemoryWorkspaceAgentRepository]:
    registry = InMemoryAgentRegistry()
    workspace = InMemoryWorkspaceAgentRepository()
    team = TeamSpaceService(
        InMemoryTeamSpaceRepository(),
        workspace,
        registry,
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
        id_generator=lambda _prefix: "space-one",
    )
    return team, registry, workspace


@pytest.mark.asyncio
async def test_sharing_creates_workspace_agent_with_release_and_current_version() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.VIEWER)
    shared_agent, release = await team.share_agent(
        "tenant-a",
        "alice",
        space.space_id,
        "alice",
        "research-agent",
        "1.0.0",
    )

    assert shared_agent.scope is AgentScope.WORKSPACE
    assert shared_agent.space_id == space.space_id
    assert shared_agent.current_version == "1.0.0"
    assert release.agent_id == shared_agent.agent_id
    assert release.source_owner_user_id == "alice"
    assert release.version == "1.0.0"

    # Sharing another version reuses the same stable identity.
    await registry.add(agent("alice", version="1.1.0"))
    shared_again, release_two = await team.share_agent(
        "tenant-a",
        "alice",
        space.space_id,
        "alice",
        "research-agent",
        "1.1.0",
        runnable_by_viewer=False,
    )
    assert shared_again.agent_id == shared_agent.agent_id
    assert release_two.version == "1.1.0"
    # The newest Release becomes the current published version.
    assert shared_again.current_version == "1.1.0"

    # The catalog shows one entry per workspace Agent with its current Release.
    accessible = await team.list_accessible_agents("tenant-a", "bob")
    assert [
        (item.space.space_id, item.agent.agent_id, item.release.version) for item in accessible
    ] == [("space-one", shared_agent.agent_id, "1.1.0")]
    assert accessible[0].version.manifest_hash == "hash-alice-1.1.0"
    assert accessible[0].can_chat is False

    # Runtime access gate resolves coordinates through Releases.
    release_row = await team.require_agent_access(
        "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
    )
    assert release_row.version == "1.0.0"


@pytest.mark.asyncio
async def test_promote_switches_current_version_without_changing_identity() -> None:
    team, registry, workspace = service()
    await registry.add(agent("alice", version="1.0.0"))
    await registry.add(agent("alice", version="1.1.0"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.CONTRIBUTOR)
    shared_agent, _ = await team.share_agent(
        "tenant-a", "alice", space.space_id, "alice", "research-agent", "1.0.0"
    )
    await team.share_agent("tenant-a", "alice", space.space_id, "alice", "research-agent", "1.1.0")
    # The most recently shared Release becomes the current version.
    stored = await workspace.get_agent("tenant-a", shared_agent.agent_id)
    assert stored.current_version == "1.1.0"

    # Contributors can promote their own Releases; viewers cannot promote.
    with pytest.raises(PermissionDeniedError):
        await team.promote_release(
            "tenant-a", "bob", space.space_id, shared_agent.agent_id, "1.0.0"
        )
    await team.promote_release("tenant-a", "alice", space.space_id, shared_agent.agent_id, "1.0.0")
    stored = await workspace.get_agent("tenant-a", shared_agent.agent_id)
    assert stored.agent_id == shared_agent.agent_id
    assert stored.current_version == "1.0.0"


@pytest.mark.asyncio
async def test_personal_release_history_and_promote_are_owner_scoped() -> None:
    team, registry, workspace = service()
    personal = WorkspaceAgent(
        tenantId="tenant-a",
        agentId="personal-agent",
        scope=AgentScope.PERSONAL,
        ownerUserId="alice",
        name="research-agent",
        currentVersion="1.1.0",
        createdBy="alice",
        createdAt=datetime(2026, 8, 3, tzinfo=UTC),
        updatedAt=datetime(2026, 8, 3, tzinfo=UTC),
    )
    await workspace.add_agent(personal)
    await registry.add(agent("alice", version="1.0.0"))
    await registry.add(agent("alice", version="1.1.0"))

    stored, releases = await team.list_personal_releases("tenant-a", "alice", personal.agent_id)
    assert stored.current_version == "1.1.0"
    assert [item.version for item in releases] == ["1.0.0", "1.1.0"]

    promoted = await team.promote_personal_release("tenant-a", "alice", personal.agent_id, "1.0.0")
    assert promoted.agent_id == personal.agent_id
    assert promoted.current_version == "1.0.0"

    with pytest.raises(NotFoundError):
        await team.list_personal_releases("tenant-a", "bob", personal.agent_id)
    with pytest.raises(NotFoundError):
        await team.promote_personal_release("tenant-a", "alice", personal.agent_id, "9.9.9")


@pytest.mark.asyncio
async def test_unsharing_last_release_clears_current_version() -> None:
    team, registry, workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    shared_agent, _ = await team.share_agent(
        "tenant-a", "alice", space.space_id, "alice", "research-agent", "1.0.0"
    )
    await team.unshare_agent("tenant-a", "alice", space.space_id, shared_agent.agent_id, "1.0.0")
    stored = await workspace.get_agent("tenant-a", shared_agent.agent_id)
    assert stored.current_version is None
    assert (
        await team.list_releases("tenant-a", "alice", space.space_id, shared_agent.agent_id) == []
    )


@pytest.mark.asyncio
async def test_viewer_run_policy_and_membership_revocation_fail_closed() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.VIEWER)
    _shared_agent, _ = await team.share_agent(
        "tenant-a",
        "alice",
        space.space_id,
        "alice",
        "research-agent",
        "1.0.0",
        runnable_by_viewer=False,
    )

    with pytest.raises(PermissionDeniedError):
        await team.require_agent_access(
            "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
        )
    await team.remove_member("tenant-a", "alice", space.space_id, "bob")
    with pytest.raises(NotFoundError):
        await team.require_agent_access(
            "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
        )


@pytest.mark.asyncio
async def test_acl_can_grant_chat_to_a_viewer() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.VIEWER)
    shared_agent, _ = await team.share_agent(
        "tenant-a",
        "alice",
        space.space_id,
        "alice",
        "research-agent",
        "1.0.0",
        runnable_by_viewer=False,
    )
    with pytest.raises(PermissionDeniedError):
        await team.require_agent_access(
            "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
        )

    # Only managers can grant ACL rows.
    with pytest.raises(PermissionDeniedError):
        await team.put_acl(
            "tenant-a",
            "bob",
            space.space_id,
            shared_agent.agent_id,
            GranteeType.USER,
            "bob",
            AgentPermission.CHAT,
        )
    await team.put_acl(
        "tenant-a",
        "alice",
        space.space_id,
        shared_agent.agent_id,
        GranteeType.USER,
        "bob",
        AgentPermission.CHAT,
    )
    release = await team.require_agent_access(
        "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
    )
    assert release.version == "1.0.0"

    permissions = await team.effective_permissions(
        "tenant-a", "bob", space.space_id, shared_agent.agent_id
    )
    assert AgentPermission.CHAT in permissions

    await team.delete_acl(
        "tenant-a",
        "alice",
        space.space_id,
        shared_agent.agent_id,
        GranteeType.USER,
        "bob",
        AgentPermission.CHAT,
    )
    with pytest.raises(PermissionDeniedError):
        await team.require_agent_access(
            "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
        )


@pytest.mark.asyncio
async def test_acl_grants_must_reference_space_members() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    shared_agent, _ = await team.share_agent(
        "tenant-a", "alice", space.space_id, "alice", "research-agent", "1.0.0"
    )
    with pytest.raises(NotFoundError):
        await team.put_acl(
            "tenant-a",
            "alice",
            space.space_id,
            shared_agent.agent_id,
            GranteeType.USER,
            "outsider",
            AgentPermission.CHAT,
        )


@pytest.mark.asyncio
async def test_fork_copies_current_release_to_personal_scope() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice", version="1.0.0"))
    await registry.add(agent("alice", version="1.1.0"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.CONTRIBUTOR)
    shared_agent, _ = await team.share_agent(
        "tenant-a", "alice", space.space_id, "alice", "research-agent", "1.0.0"
    )
    await team.share_agent("tenant-a", "alice", space.space_id, "alice", "research-agent", "1.1.0")
    # Fork resolves the current Release (1.1.0) of the workspace Agent.
    fork = await team.fork_agent("tenant-a", "bob", space.space_id, shared_agent.agent_id)
    assert fork.owner_user_id == "bob"
    assert fork.version == "1.1.0"
    assert fork.manifest_hash == "hash-alice-1.1.0"
    original = await registry.get("tenant-a", "alice", "research-agent", "1.1.0")
    assert original.owner_user_id == "alice"


@pytest.mark.asyncio
async def test_contributor_cannot_share_another_users_agent() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.CONTRIBUTOR)
    with pytest.raises(PermissionDeniedError):
        await team.share_agent(
            "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
        )


@pytest.mark.asyncio
async def test_user_group_grants_batch_agent_access() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    await team.put_member("tenant-a", "alice", space.space_id, "bob", SpaceRole.VIEWER)
    await team.put_member("tenant-a", "alice", space.space_id, "carol", SpaceRole.VIEWER)
    shared_agent, _ = await team.share_agent(
        "tenant-a",
        "alice",
        space.space_id,
        "alice",
        "research-agent",
        "1.0.0",
        runnable_by_viewer=False,
    )

    group = await team.create_group("tenant-a", "alice", "法务协助组")
    await team.add_group_member("tenant-a", group.group_id, "bob")
    await team.add_group_member("tenant-a", group.group_id, "carol")

    # Group ACL grants chat to every member of the group.
    await team.put_acl(
        "tenant-a",
        "alice",
        space.space_id,
        shared_agent.agent_id,
        GranteeType.GROUP,
        group.group_id,
        AgentPermission.CHAT,
    )
    release = await team.require_agent_access(
        "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
    )
    assert release.version == "1.0.0"
    release = await team.require_agent_access(
        "tenant-a", "carol", space.space_id, "alice", "research-agent", "1.0.0"
    )
    assert release.version == "1.0.0"

    # Removing a member from the group revokes the grant for that user only.
    await team.remove_group_member("tenant-a", group.group_id, "carol")
    with pytest.raises(PermissionDeniedError):
        await team.require_agent_access(
            "tenant-a", "carol", space.space_id, "alice", "research-agent", "1.0.0"
        )
    release = await team.require_agent_access(
        "tenant-a", "bob", space.space_id, "alice", "research-agent", "1.0.0"
    )
    assert release.version == "1.0.0"


@pytest.mark.asyncio
async def test_group_acl_grant_requires_existing_group() -> None:
    team, registry, _workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    shared_agent, _ = await team.share_agent(
        "tenant-a", "alice", space.space_id, "alice", "research-agent", "1.0.0"
    )
    with pytest.raises(NotFoundError):
        await team.put_acl(
            "tenant-a",
            "alice",
            space.space_id,
            shared_agent.agent_id,
            GranteeType.GROUP,
            "group_missing",
            AgentPermission.CHAT,
        )


async def _personal_agent(
    workspace: InMemoryWorkspaceAgentRepository, owner: str, name: str = "research-agent"
) -> str:
    now = datetime(2026, 8, 3, tzinfo=UTC)
    agent_row = WorkspaceAgent(
        tenantId="tenant-a",
        agentId=f"agent_{owner}_{name}",
        scope=AgentScope.PERSONAL,
        ownerUserId=owner,
        name=name,
        createdBy=owner,
        createdAt=now,
        updatedAt=now,
    )
    await workspace.add_agent(agent_row)
    return agent_row.agent_id


@pytest.mark.asyncio
async def test_transfer_personal_agent_rekeys_versions_and_preserves_identity() -> None:
    from harness.studio.models import AgentDraft
    from harness.studio.repositories import InMemoryAgentDraftRepository

    team, registry, workspace = service()
    drafts = InMemoryAgentDraftRepository()
    team._drafts = drafts
    await registry.add(agent("alice", version="1.0.0"))
    await registry.add(agent("alice", version="1.1.0"))
    draft = AgentDraft(
        draftId="draft-1",
        tenantId="tenant-a",
        revision=1,
        spec=AgentDraftSpec(
            name="research-agent",
            version="2.0.0",
            displayName="Research Agent",
            description="research",
            domain="research",
            template=AgentTemplate.ANALYST,
            model=DraftModelSelection(routeId="route-1", model="model-1"),
            systemPrompt="## Mission\npurpose",
            skills=(DraftSkill(name="skill-1", description="d", instructions="i"),),
            permissionPolicy="production-standard",
            evaluationCases=(
                EvalCase(
                    id="case-1",
                    tags=("happy",),
                    prompt="prompt",
                ),
            ),
        ),
        createdBy="alice",
        updatedBy="alice",
        createdAt=datetime(2026, 8, 3, tzinfo=UTC),
        updatedAt=datetime(2026, 8, 3, tzinfo=UTC),
    )
    await drafts.add(draft)
    personal_id = await _personal_agent(workspace, "alice")

    transferred = await team.transfer_agent("tenant-a", "alice", personal_id, to_user_id="bob")
    assert transferred.owner_user_id == "bob"
    assert transferred.agent_id == personal_id

    moved = await registry.get("tenant-a", "bob", "research-agent", "1.1.0")
    assert moved.owner_user_id == "bob"
    with pytest.raises(NotFoundError):
        await registry.get("tenant-a", "alice", "research-agent", "1.0.0")
    moved_draft = await drafts.get("tenant-a", "bob", "draft-1")
    assert moved_draft.created_by == "bob"
    # The new owner can publish more versions under the same identity.
    assert await workspace.get_personal_agent("tenant-a", "bob", "research-agent") is not None


@pytest.mark.asyncio
async def test_transfer_conflicts_and_permissions_fail_closed() -> None:
    team, registry, workspace = service()
    await registry.add(agent("alice"))
    await registry.add(agent("bob"))
    alice_agent_id = await _personal_agent(workspace, "alice")
    await _personal_agent(workspace, "bob")

    with pytest.raises(PermissionDeniedError):
        await team.transfer_agent("tenant-a", "bob", alice_agent_id, to_user_id="bob")
    with pytest.raises(ConflictError):
        # bob already owns research-agent@1.0.0 -> version coordinate conflict.
        await team.transfer_agent("tenant-a", "alice", alice_agent_id, to_user_id="bob")
    with pytest.raises(ConflictError):
        await team.transfer_agent("tenant-a", "alice", alice_agent_id)


@pytest.mark.asyncio
async def test_transfer_personal_to_workspace_hands_up_identity() -> None:
    team, registry, workspace = service()
    await registry.add(agent("alice"))
    space = await team.create("tenant-a", "alice", "调查组")
    alice_agent_id = await _personal_agent(workspace, "alice")

    transferred = await team.transfer_agent(
        "tenant-a", "alice", alice_agent_id, to_space_id=space.space_id
    )
    assert transferred.scope is AgentScope.WORKSPACE
    assert transferred.space_id == space.space_id
    assert transferred.owner_user_id is None
    assert transferred.agent_id == alice_agent_id
    # Immutable versions stay with their creator coordinates for Release lookup.
    original = await registry.get("tenant-a", "alice", "research-agent", "1.0.0")
    assert original.owner_user_id == "alice"
