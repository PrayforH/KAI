import hashlib
import io
import tarfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.adapters.memory import (
    InMemoryArtifactStore,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryWorkspaceSnapshotRepository,
)
from harness.application.events import EventService
from harness.application.workspaces import WorkspaceService
from harness.core.models import Run, RunStatus, Session, WorkspaceSnapshot
from harness.quota.models import QuotaResource, ReplaceQuotaPolicyRequest
from harness.quota.repositories import InMemoryQuotaRepository, QuotaExceededError
from harness.quota.service import QuotaService
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.sandbox.local import LocalSandboxProvider
from harness.worker.orchestrator import RunOrchestrator


@pytest.mark.asyncio
async def test_workspace_archive_and_restore_round_trip(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    snapshots = InMemoryWorkspaceSnapshotRepository()
    service = WorkspaceService(store, snapshots=snapshots)
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("input")
    (source / "nested").mkdir()
    (source / "nested" / "result.txt").write_text("result")

    snapshot = await service.archive(tenant_id="tenant-a", session_id="session-1", workspace=source)
    target = tmp_path / "target"
    target.mkdir()
    await service.restore(snapshot, workspace=target)

    assert (target / "input.txt").read_text() == "input"
    assert (target / "nested" / "result.txt").read_text() == "result"
    assert await snapshots.latest("tenant-a", "session-1") == snapshot


@pytest.mark.asyncio
async def test_restore_latest_returns_none_then_restores_authoritative_snapshot(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    snapshots = InMemoryWorkspaceSnapshotRepository()
    service = WorkspaceService(store, snapshots=snapshots)
    target = tmp_path / "target"
    target.mkdir()

    assert (
        await service.restore_latest(tenant_id="tenant-a", session_id="session-a", workspace=target)
        is None
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "state.txt").write_text("restored")
    snapshot = await service.archive(tenant_id="tenant-a", session_id="session-a", workspace=source)

    restored = await service.restore_latest(
        tenant_id="tenant-a", session_id="session-a", workspace=target
    )

    assert restored == snapshot
    assert (target / "state.txt").read_text() == "restored"


@pytest.mark.asyncio
async def test_archive_rejects_oversize_and_excessive_member_workspaces(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    oversized = tmp_path / "oversized"
    oversized.mkdir()
    (oversized / "large.bin").write_bytes(b"x" * 9)

    with pytest.raises(ValueError, match="size limit"):
        await WorkspaceService(store, max_archive_bytes=8).archive(
            tenant_id="tenant-a",
            session_id="session-a",
            workspace=oversized,
        )

    crowded = tmp_path / "crowded"
    crowded.mkdir()
    (crowded / "one").touch()
    (crowded / "two").touch()

    with pytest.raises(ValueError, match="member limit"):
        await WorkspaceService(store, max_archive_members=1).archive(
            tenant_id="tenant-a",
            session_id="session-a",
            workspace=crowded,
        )


@pytest.mark.asyncio
async def test_archive_omits_recreated_runtime_assets_and_dependency_trees(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    snapshots = InMemoryWorkspaceSnapshotRepository()
    service = WorkspaceService(
        store,
        snapshots=snapshots,
        max_archive_members=2,
    )
    source = tmp_path / "source"
    output = source / "outputs"
    output.mkdir(parents=True)
    (output / "graph.html").write_text("<canvas></canvas>")
    for root_name in ("node_modules", ".claude", "inputs"):
        for index in range(10):
            generated = source / root_name / f"package-{index}"
            generated.mkdir(parents=True)
            (generated / "generated.js").write_text("generated")

    snapshot = await service.archive(
        tenant_id="tenant-a",
        session_id="session-a",
        workspace=source,
    )
    restored = tmp_path / "restored"
    await service.restore(snapshot, workspace=restored)

    assert (restored / "outputs/graph.html").read_text() == "<canvas></canvas>"
    assert not (restored / "node_modules").exists()
    assert not (restored / ".claude").exists()
    assert not (restored / "inputs").exists()


@pytest.mark.asyncio
async def test_snapshot_quota_rejects_before_object_and_metadata_are_persisted(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    snapshots = InMemoryWorkspaceSnapshotRepository()
    quotas = QuotaService(InMemoryQuotaRepository())
    await quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner-a",
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            limits={QuotaResource.SNAPSHOT_BYTES: 1},
        ),
    )
    workspace = tmp_path / "quota-workspace"
    workspace.mkdir()
    (workspace / "report.txt").write_text("evidence" * 100)

    with pytest.raises(QuotaExceededError, match="snapshot_bytes"):
        await WorkspaceService(
            store,
            snapshots=snapshots,
            quotas=quotas,
        ).archive(
            tenant_id="tenant-a",
            session_id="session-a",
            workspace=workspace,
        )

    assert await snapshots.latest("tenant-a", "session-a") is None
    assert vars(store)["_items"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("member_kind", ["traversal", "symlink"])
async def test_restore_rejects_unsafe_archive_members(tmp_path: Path, member_kind: str) -> None:
    store = InMemoryArtifactStore()
    service = WorkspaceService(store)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        if member_kind == "traversal":
            member = tarfile.TarInfo("../escape.txt")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        else:
            member = tarfile.TarInfo("escape-link")
            member.type = tarfile.SYMTYPE
            member.linkname = "../escape.txt"
            archive.addfile(member)
    content = buffer.getvalue()
    stored = await store.put("tenant-a", "snapshot-unsafe", content)
    snapshot = WorkspaceSnapshot(
        snapshot_id="snapshot-unsafe",
        session_id="session-a",
        tenant_id="tenant-a",
        object_key=stored.object_key,
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="unsafe"):
        await service.restore(snapshot, workspace=tmp_path / "target")


@pytest.mark.asyncio
async def test_restore_rejects_corrupt_archive_even_when_hash_matches(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    service = WorkspaceService(store)
    content = b"not-a-tar"
    stored = await store.put("tenant-a", "snapshot-corrupt", content)
    snapshot = WorkspaceSnapshot(
        snapshot_id="snapshot-corrupt",
        session_id="session-a",
        tenant_id="tenant-a",
        object_key=stored.object_key,
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="invalid workspace archive"):
        await service.restore(snapshot, workspace=tmp_path / "target")


@pytest.mark.asyncio
async def test_later_run_restores_latest_snapshot_before_runtime(
    tmp_path: Path,
) -> None:
    class StatefulRuntime:
        def __init__(self) -> None:
            self.executions = 0
            self.restored_content: str | None = None

        async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
            self.executions += 1
            state = context.workspace / "state.txt"
            if self.executions == 1:
                state.write_text("from-first-run")
            else:
                self.restored_content = state.read_text()
            yield RuntimeEvent(type="message.completed")

    now = datetime.now(UTC)
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    snapshots = InMemoryWorkspaceSnapshotRepository()
    store = InMemoryArtifactStore()
    runtime = StatefulRuntime()
    session = Session(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="alice",
        agent_name="agent-a",
        agent_version="1.0.0",
        created_at=now,
    )
    await sessions.add(session)
    for run_id in ("run-1", "run-2"):
        await runs.add(
            Run(
                run_id=run_id,
                session_id=session.session_id,
                tenant_id=session.tenant_id,
                status=RunStatus.QUEUED,
                idempotency_key=run_id,
                created_at=now,
                updated_at=now,
            )
        )
    sequence = 0

    def ids(prefix: str) -> str:
        nonlocal sequence
        sequence += 1
        return f"{prefix}-{sequence}"

    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=EventService(events, InMemoryEventBus(), clock=lambda: now, id_generator=ids),
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path / "sandboxes"),
        clock=lambda: now,
        workspaces=WorkspaceService(store, snapshots=snapshots),
    )

    assert (await orchestrator.execute("tenant-a", "run-1")).status is RunStatus.SUCCEEDED
    assert (await orchestrator.execute("tenant-a", "run-2")).status is RunStatus.SUCCEEDED

    assert runtime.restored_content == "from-first-run"
    second_events = await events.list_after("tenant-a", "run-2", 0)
    assert [event.type for event in second_events[:4]] == [
        "run.provisioning",
        "sandbox.provisioned",
        "workspace.restored",
        "run.running",
    ]
