from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import (
    InMemoryAguiThreadBindingRepository,
    InMemoryThreadFileRepository,
    InMemoryUserMemoryRepository,
    InMemoryWorkspaceSnapshotRepository,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import (
    AguiThreadBinding,
    ThreadFile,
    ThreadFileKind,
    UserMemory,
    WorkspaceSnapshot,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


@pytest.mark.asyncio
async def test_user_memory_is_scoped_and_compare_and_set_is_versioned() -> None:
    repository = InMemoryUserMemoryRepository()
    original = UserMemory(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        content="first",
        version=1,
        updated_at=NOW,
    )
    await repository.add(original)

    assert await repository.get("tenant-a", "user-a", "agent-a") == original
    assert await repository.get("tenant-a", "user-b", "agent-a") is None
    updated = original.model_copy(
        update={"content": "second", "version": 2, "updated_at": NOW + timedelta(seconds=1)}
    )
    assert await repository.compare_and_set(1, updated) is True
    assert await repository.compare_and_set(1, updated) is False
    with pytest.raises(ConflictError):
        await repository.add(original)


@pytest.mark.asyncio
async def test_thread_file_catalog_preserves_scope_and_lineage() -> None:
    repository = InMemoryThreadFileRepository()
    original = ThreadFile(
        file_id="original",
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        kind=ThreadFileKind.ORIGINAL,
        name="facts.txt",
        media_type="text/plain",
        path="inputs/original/facts.txt",
        created_at=NOW,
    )
    derived = original.model_copy(
        update={
            "file_id": "derived",
            "kind": ThreadFileKind.DERIVED,
            "name": "facts.md",
            "path": "inputs/processed/facts/facts.md",
            "parent_file_id": original.file_id,
        }
    )
    await repository.add(original)
    await repository.add(derived)

    assert await repository.list_for_session("tenant-a", "user-a", "session-a") == [
        original,
        derived,
    ]
    assert await repository.list_for_session("tenant-a", "user-b", "session-a") == []
    assert await repository.list_children("tenant-a", original.file_id) == [derived]


@pytest.mark.asyncio
async def test_workspace_snapshot_repository_returns_latest_for_session() -> None:
    repository = InMemoryWorkspaceSnapshotRepository()
    older = WorkspaceSnapshot(
        snapshot_id="snapshot-1",
        session_id="session-a",
        tenant_id="tenant-a",
        object_key="tenant-a/snapshot-1",
        sha256="a" * 64,
        created_at=NOW,
    )
    newer = older.model_copy(
        update={
            "snapshot_id": "snapshot-2",
            "object_key": "tenant-a/snapshot-2",
            "created_at": NOW + timedelta(seconds=1),
        }
    )
    await repository.add(newer)
    await repository.add(older)

    assert await repository.latest("tenant-a", "session-a") == newer
    assert await repository.latest("tenant-b", "session-a") is None


@pytest.mark.asyncio
async def test_agui_binding_round_trips_in_both_directions() -> None:
    repository = InMemoryAguiThreadBindingRepository()
    binding = AguiThreadBinding(
        tenant_id="tenant-a",
        user_id="user-a",
        thread_id="thread-a",
        session_id="session-a",
        created_at=NOW,
        updated_at=NOW,
    )
    await repository.add(binding)

    assert await repository.get_by_thread("tenant-a", "user-a", "thread-a") == binding
    assert await repository.get_by_session("tenant-a", "user-a", "session-a") == binding
    titled = await repository.update_title(
        "tenant-a",
        "user-a",
        "thread-a",
        title="生成可下载报告",
        source="model",
        generated_at=NOW + timedelta(seconds=1),
    )
    assert titled.title == "生成可下载报告"
    assert titled.title_source == "model"
    assert await repository.get_by_session(
        "tenant-a", "user-a", "session-a"
    ) == titled
    rebound = await repository.rebind_session(
        "tenant-a",
        "user-a",
        "thread-a",
        expected_session_id="session-a",
        session_id="session-b",
        updated_at=NOW + timedelta(seconds=2),
    )
    assert rebound.session_id == "session-b"
    assert rebound.previous_session_ids == ("session-a",)
    assert await repository.get_by_session(
        "tenant-a", "user-a", "session-a"
    ) == rebound
    assert await repository.get_by_session(
        "tenant-a", "user-a", "session-b"
    ) == rebound
    with pytest.raises(ConflictError, match="changed concurrently"):
        await repository.rebind_session(
            "tenant-a",
            "user-a",
            "thread-a",
            expected_session_id="session-a",
            session_id="session-c",
            updated_at=NOW + timedelta(seconds=3),
        )
    archived = await repository.set_archived(
        "tenant-a",
        "user-a",
        "thread-a",
        archived_at=NOW + timedelta(seconds=4),
    )
    assert archived.archived_at == NOW + timedelta(seconds=4)
    assert await repository.list_for_user(
        "tenant-a", "user-a", limit=10
    ) == []
    assert await repository.list_for_user(
        "tenant-a", "user-a", limit=10, archived=True
    ) == [archived]
    restored = await repository.set_archived(
        "tenant-a", "user-a", "thread-a", archived_at=None
    )
    assert restored.archived_at is None
    assert await repository.list_for_user(
        "tenant-a", "user-a", limit=10
    ) == [restored]
    with pytest.raises(NotFoundError):
        await repository.get_by_thread("tenant-a", "user-b", "thread-a")
