from datetime import UTC, datetime

import pytest

from harness.adapters.memory import (
    InMemoryAgentRegistry,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryTaskQueue,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    Run,
    RunStatus,
    Session,
)
from harness.core.ports import RunTask


def now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_agent_registry_is_tenant_scoped_and_rejects_duplicates() -> None:
    registry = InMemoryAgentRegistry()
    version = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-1",
        name="echo-agent",
        version="1.0.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash="a" * 64,
        created_at=now(),
    )

    await registry.add(version)

    assert await registry.get("tenant-a", "user-1", "echo-agent", "1.0.0") == version
    with pytest.raises(NotFoundError):
        await registry.get("tenant-b", "user-1", "echo-agent", "1.0.0")
    with pytest.raises(ConflictError):
        await registry.add(version)


@pytest.mark.asyncio
async def test_session_repository_is_tenant_scoped() -> None:
    repository = InMemorySessionRepository()
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=now(),
    )

    await repository.add(session)

    assert await repository.get("tenant-a", "session-1") == session
    assert await repository.list_for_ids("tenant-a", ["session-1", "session-1"]) == [
        session,
        session,
    ]
    assert await repository.list_for_ids("tenant-a", []) == []
    with pytest.raises(NotFoundError):
        await repository.get("tenant-b", "session-1")
    with pytest.raises(NotFoundError):
        await repository.list_for_ids("tenant-a", ["session-1", "missing"])


@pytest.mark.asyncio
async def test_session_repository_binds_claude_session_once() -> None:
    repository = InMemorySessionRepository()
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=now(),
    )
    await repository.add(session)

    bound = await repository.bind_claude_session_id("tenant-a", "session-1", "claude-session-1")

    assert bound.claude_session_id == "claude-session-1"
    assert (
        await repository.bind_claude_session_id("tenant-a", "session-1", "claude-session-1")
    ) == bound
    with pytest.raises(ConflictError, match="already bound"):
        await repository.bind_claude_session_id("tenant-a", "session-1", "claude-session-2")
    cleared = await repository.clear_claude_session_id("tenant-a", "session-1", "claude-session-1")
    assert cleared.claude_session_id is None
    rebound = await repository.bind_claude_session_id("tenant-a", "session-1", "claude-session-2")
    assert rebound.claude_session_id == "claude-session-2"
    assert rebound.runtime_thread_id == "claude-session-2"


@pytest.mark.asyncio
async def test_session_repository_binds_runtime_thread_once() -> None:
    repository = InMemorySessionRepository()
    session = Session(
        session_id="session-codex",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        runtime_type="codex-app-server",
        created_at=now(),
    )
    await repository.add(session)

    bound = await repository.bind_runtime_thread(
        "tenant-a",
        "session-codex",
        "codex-app-server",
        "thread-codex-1",
    )

    assert bound.runtime_thread_id == "thread-codex-1"
    assert bound.claude_session_id is None
    assert (
        await repository.bind_runtime_thread(
            "tenant-a",
            "session-codex",
            "codex-app-server",
            "thread-codex-1",
        )
        == bound
    )
    with pytest.raises(ConflictError, match="already bound"):
        await repository.bind_runtime_thread(
            "tenant-a",
            "session-codex",
            "codex-app-server",
            "thread-codex-2",
        )
    with pytest.raises(ConflictError, match="pinned to runtime"):
        await repository.bind_runtime_thread(
            "tenant-a",
            "session-codex",
            "claude-agent-sdk",
            "claude-session-1",
        )

    cleared = await repository.clear_runtime_thread(
        "tenant-a",
        "session-codex",
        "codex-app-server",
        "thread-codex-1",
    )
    assert cleared.runtime_thread_id is None


@pytest.mark.asyncio
async def test_run_repository_compare_and_set_prevents_stale_writes() -> None:
    repository = InMemoryRunRepository()
    timestamp = now()
    run = Run(
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.QUEUED,
        idempotency_key="idem-1",
        created_at=timestamp,
        updated_at=timestamp,
    )
    await repository.add(run)
    provisioning = run.model_copy(update={"status": RunStatus.PROVISIONING, "fencing_token": 1})

    assert await repository.compare_and_set(RunStatus.QUEUED, provisioning) is True
    assert await repository.compare_and_set(RunStatus.QUEUED, provisioning) is False
    reclaimed = provisioning.model_copy(update={"fencing_token": 2})
    assert await repository.compare_and_set(RunStatus.PROVISIONING, reclaimed) is True
    assert await repository.compare_and_set(RunStatus.PROVISIONING, reclaimed) is False
    assert (await repository.get("tenant-a", "run-1")).status is RunStatus.PROVISIONING


@pytest.mark.asyncio
async def test_task_queue_is_idempotent() -> None:
    queue = InMemoryTaskQueue()

    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    await queue.enqueue(task)
    await queue.enqueue(task)

    assert await queue.dequeue() == task
    assert await queue.dequeue() is None
    await queue.acknowledge(task)
    await queue.enqueue(task)
    assert await queue.dequeue() == task
