from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.core.errors import ConflictError, NotFoundError
from harness.core.events import RunEvent
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    AguiThreadBinding,
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    ArtifactStatus,
    InputArtifact,
    Run,
    RunStatus,
    Session,
    ThreadFile,
    ThreadFileKind,
    UserMemory,
    WorkspaceSnapshot,
)
from harness.storage.database import SessionFactory
from harness.storage.platform_repositories import (
    PostgresAgentRegistry,
    PostgresAguiThreadBindingRepository,
    PostgresApprovalRepository,
    PostgresArtifactRepository,
    PostgresInputArtifactRepository,
    PostgresSessionRepository,
    PostgresThreadFileRepository,
    PostgresUserMemoryRepository,
    PostgresWorkspaceSnapshotRepository,
)
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository

DatabaseFixture = tuple[AsyncEngine, SessionFactory]


@pytest.mark.asyncio
async def test_postgres_run_fencing_and_idempotency(database: DatabaseFixture) -> None:
    _, sessions = database
    repository = PostgresRunRepository(sessions)
    now = datetime.now(UTC)
    run = Run(
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.QUEUED,
        idempotency_key="idem-1",
        created_at=now,
        updated_at=now,
    )
    await repository.add(run)

    assert await repository.find_by_idempotency_key("tenant-a", "session-1", "idem-1") == run
    updated = run.model_copy(update={"status": RunStatus.PROVISIONING, "fencing_token": 1})
    assert await repository.compare_and_set(RunStatus.QUEUED, updated) is True
    assert await repository.compare_and_set(RunStatus.QUEUED, updated) is False


@pytest.mark.asyncio
async def test_postgres_outbox_events_remain_ordered(database: DatabaseFixture) -> None:
    _, sessions = database
    repository = PostgresEventRepository(sessions)
    now = datetime.now(UTC)
    first = RunEvent(
        event_id="event-1",
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        sequence=1,
        type="run.queued",
        timestamp=now,
    )
    second = first.model_copy(update={"event_id": "event-2", "sequence": 2, "type": "run.running"})
    await repository.append(first)
    await repository.append(second)

    assert await repository.list_after("tenant-a", "run-1", 0) == [first, second]

    observed = second.model_copy(
        update={
            "event_id": "event-3",
            "run_id": "run-2",
            "sequence": 1,
            "type": "context.window.observed",
            "timestamp": now + timedelta(microseconds=1),
        }
    )
    await repository.append(observed)

    assert (
        await repository.latest_for_session_type("tenant-a", "session-1", "context.window.observed")
        == observed
    )
    assert (
        await repository.latest_for_session_type(
            "tenant-a", "session-other", "context.window.observed"
        )
        is None
    )
    assert (
        await repository.latest_for_session_types(
            "tenant-a",
            "session-1",
            ("context.window.observed", "context.window.unavailable"),
        )
        == observed
    )


@pytest.mark.asyncio
async def test_postgres_platform_repositories_are_durable_and_tenant_scoped(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    now = datetime.now(UTC)
    agent = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="agent-a",
        version="1.0.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash="a" * 64,
        snapshot={
            "manifest": {"metadata": {"name": "agent-a", "version": "1.0.0"}},
            "files": {"large.bin": "x" * 1024},
            "skill_snapshots": [
                {
                    "name": "skill-creator",
                    "description": "Create",
                    "instructions": "private",
                    "files": [],
                }
            ],
        },
        created_at=now,
    )
    agent_repository = PostgresAgentRegistry(sessions)
    await agent_repository.add(agent)
    assert await agent_repository.get("tenant-a", "user-a", "agent-a", "1.0.0") == agent
    catalog_versions = await agent_repository.list_catalog_for_user("tenant-a", "user-a")
    assert len(catalog_versions) == 1
    assert catalog_versions[0].model_copy(update={"snapshot": agent.snapshot}) == agent
    assert catalog_versions[0].snapshot == {
        "manifest": agent.snapshot["manifest"],
        "skill_snapshots": [{"name": "skill-creator", "description": "Create"}],
    }

    thread = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        agent_version="1.0.0",
        created_at=now,
    )
    session_repository = PostgresSessionRepository(sessions)
    await session_repository.add(thread)
    assert await session_repository.get("tenant-a", "session-a") == thread
    assert await session_repository.list_for_ids("tenant-a", ["session-a", "session-a"]) == [
        thread,
        thread,
    ]
    assert await session_repository.list_for_ids("tenant-a", []) == []
    with pytest.raises(NotFoundError):
        await session_repository.list_for_ids("tenant-a", ["session-a", "missing"])
    bound_thread = await session_repository.bind_claude_session_id(
        "tenant-a", "session-a", "claude-session-a"
    )
    assert bound_thread.claude_session_id == "claude-session-a"
    assert await session_repository.get("tenant-a", "session-a") == bound_thread
    assert (
        await session_repository.bind_claude_session_id("tenant-a", "session-a", "claude-session-a")
    ) == bound_thread
    with pytest.raises(ConflictError, match="already bound"):
        await session_repository.bind_claude_session_id("tenant-a", "session-a", "claude-session-b")
    cleared_thread = await session_repository.clear_claude_session_id(
        "tenant-a", "session-a", "claude-session-a"
    )
    assert cleared_thread.claude_session_id is None
    rebound_thread = await session_repository.bind_claude_session_id(
        "tenant-a", "session-a", "claude-session-b"
    )
    assert rebound_thread.claude_session_id == "claude-session-b"

    approval = ApprovalRequest(
        approval_id="approval-a",
        run_id="run-a",
        tenant_id="tenant-a",
        tool_call_id="tool-a",
        status=ApprovalStatus.PENDING,
        reason="verify",
        expires_at=now,
        created_at=now,
    )
    approval_repository = PostgresApprovalRepository(sessions)
    await approval_repository.add(approval)
    assert await approval_repository.list_expired_pending(now, limit=10) == [approval]
    approved = approval.model_copy(update={"status": ApprovalStatus.APPROVED})
    assert await approval_repository.compare_and_set(ApprovalStatus.PENDING, approved)
    assert not await approval_repository.compare_and_set(ApprovalStatus.PENDING, approved)

    artifact = Artifact(
        artifact_id="artifact-a",
        run_id="run-a",
        tenant_id="tenant-a",
        name="result.txt",
        media_type="text/plain",
        status=ArtifactStatus.READY,
        object_key="tenant-a/artifact-a",
    )
    artifact_repository = PostgresArtifactRepository(sessions)
    await artifact_repository.add(artifact)
    assert await artifact_repository.list_for_run("tenant-a", "run-a") == [artifact]

    input_artifact = InputArtifact(
        input_artifact_id="input-a",
        tenant_id="tenant-a",
        user_id="user-a",
        name="facts.txt",
        media_type="text/plain",
        status=ArtifactStatus.READY,
        object_key="tenant-a/input-a",
        created_at=now,
    )
    input_repository = PostgresInputArtifactRepository(sessions)
    await input_repository.add(input_artifact)
    assert await input_repository.get("tenant-a", "input-a") == input_artifact

    memory = UserMemory(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        content="remember",
        version=1,
        updated_at=now,
    )
    memory_repository = PostgresUserMemoryRepository(sessions)
    await memory_repository.add(memory)
    memory_v2 = memory.model_copy(update={"content": "updated", "version": 2})
    assert await memory_repository.compare_and_set(1, memory_v2)
    assert await memory_repository.get("tenant-a", "user-a", "agent-a") == memory_v2

    source = ThreadFile(
        file_id="file-a",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        kind=ThreadFileKind.ORIGINAL,
        name="facts.txt",
        media_type="text/plain",
        path="inputs/original/facts.txt",
        created_at=now,
    )
    file_repository = PostgresThreadFileRepository(sessions)
    await file_repository.add(source)
    assert await file_repository.list_for_session("tenant-a", "user-a", "session-a") == [source]

    snapshot = WorkspaceSnapshot(
        snapshot_id="snapshot-a",
        session_id="session-a",
        tenant_id="tenant-a",
        object_key="tenant-a/snapshot-a",
        sha256="b" * 64,
        created_at=now,
    )
    snapshot_repository = PostgresWorkspaceSnapshotRepository(sessions)
    await snapshot_repository.add(snapshot)
    assert await snapshot_repository.latest("tenant-a", "session-a") == snapshot

    binding = AguiThreadBinding(
        tenant_id="tenant-a",
        user_id="user-a",
        thread_id="thread-a",
        session_id="session-a",
        created_at=now,
        updated_at=now,
    )
    binding_repository = PostgresAguiThreadBindingRepository(sessions)
    await binding_repository.add(binding)
    assert await binding_repository.get_by_thread("tenant-a", "user-a", "thread-a") == binding
    titled = await binding_repository.update_title(
        "tenant-a",
        "user-a",
        "thread-a",
        title="生成可下载报告",
        source="model",
        generated_at=now,
    )
    assert titled.title == "生成可下载报告"
    assert (
        await binding_repository.get_by_thread("tenant-a", "user-a", "thread-a")
    ).title == "生成可下载报告"
    rebound = await binding_repository.rebind_session(
        "tenant-a",
        "user-a",
        "thread-a",
        expected_session_id="session-a",
        session_id="session-b",
        updated_at=now,
    )
    assert rebound.previous_session_ids == ("session-a",)
    assert await binding_repository.get_by_session("tenant-a", "user-a", "session-a") == rebound
    assert await binding_repository.get_by_session("tenant-a", "user-a", "session-b") == rebound
