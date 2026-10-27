"""Real Computer ownership + Worker orchestration, simulated physical commands.

The host driver uses real fixture files. Linux namespace/Docker acceptance is
separate; these tests do not claim to execute a model or remote OS process.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from harness.adapters.memory import InMemoryArtifactStore
from harness.application.workspaces import WorkspaceService
from harness.computer.docker import DockerComputerDriver
from harness.computer.manager import ComputerAllocation
from harness.computer.models import ComputerState
from harness.computer.registry import ComputerRegistry
from harness.computer.sandbox import ComputerSandboxProvider
from harness.computer.workspace_guest import Workspace
from harness.core.errors import NotFoundError
from harness.core.models import RunStatus, Session
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.runtime.fake import FakeRuntime
from harness.sandbox.base import SandboxCommandResult
from tests.integration.storage.conftest import DatabaseFixture
from tests.integration.storage.test_computer_manager_postgres import Driver
from tests.unit.worker.test_orchestrator import NOW, arrange


class FileDriver(Driver):
    provider = "docker-computer"

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.root.mkdir()
        self.files = Workspace(root)

    async def execute(
        self,
        allocation: ComputerAllocation,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
        stdin: bytes | None = None,
    ) -> SandboxCommandResult:
        if argv[0] == "fixture-write":
            if allocation.token.run_id == "run-2":
                assert (self.root / "project.txt").read_text() == "run-1"
            (self.root / "outputs").mkdir(exist_ok=True)
            (self.root / "outputs/report.txt").write_text(cast(str, allocation.token.run_id))
            (self.root / "project.txt").write_text(cast(str, allocation.token.run_id))
            return SandboxCommandResult(exit_code=0)
        assert argv[:3] == ["python3", "-I", "-c"] and stdin is not None
        result = self.files.invoke(argv[-1], json.loads(stdin))
        return SandboxCommandResult(exit_code=0, stdout=json.dumps(result))


class FileRuntime(FakeRuntime):
    def __init__(self, *, hang: bool = False):
        super().__init__()
        self.hang = hang
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        assert context.sandbox_command_executor is not None
        await context.sandbox_command_executor(["fixture-write"], None, 30)
        self.started.set()
        try:
            if self.hang:
                await asyncio.Event().wait()
            async for event in super().execute(context):
                yield event
        finally:
            self.cancelled = self.hang


def provider(database: DatabaseFixture, tmp_path: Path, *, ttl: int = 10):
    _, sessions = database
    registry = ComputerRegistry(sessions, lease_ttl_seconds=ttl)
    driver = FileDriver(tmp_path / "owner-disk")
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    sandbox = ComputerSandboxProvider(registry, cast(DockerComputerDriver, driver), session)
    return registry, driver, sandbox


@pytest.mark.asyncio
async def test_worker_two_runs_retain_owner_files_without_restoring_or_archiving_partial_mirror(
    database: DatabaseFixture,
    tmp_path: Path,
):
    registry, driver, sandbox = provider(database, tmp_path)
    (driver.root / "outputs").mkdir()
    (driver.root / "outputs/previous.txt").write_text("older result")
    workspaces = WorkspaceService(InMemoryArtifactStore())
    restore, archive = AsyncMock(), AsyncMock()
    workspaces.restore_latest = restore
    workspaces.archive = archive
    orchestrator, _, runs, events = await arrange(
        tmp_path,
        sandbox_override=sandbox,
        runtime_override=FileRuntime(),
        workspaces=workspaces,
        enable_artifacts=True,
    )
    first = await orchestrator.execute("tenant-a", "run-1")
    before = await registry.get("tenant-a", "user-1")
    second = first.model_copy(
        update={
            "run_id": "run-2",
            "idempotency_key": "idem-2",
            "status": RunStatus.QUEUED,
            "fencing_token": 0,
        }
    )
    await runs.add(second)
    result = await orchestrator.execute("tenant-a", "run-2")
    after = await registry.get("tenant-a", "user-1")
    assert result.status is first.status is RunStatus.SUCCEEDED
    assert before.computer_id == after.computer_id and before.provider_ref == after.provider_ref
    assert before.volume_ref == after.volume_ref and after.state is ComputerState.IDLE
    assert driver.calls.count("start") == 1 and driver.calls.count("connect") == 2
    assert driver.calls.count("finish") == 2
    assert (driver.root / "project.txt").read_text() == "run-2"
    restore.assert_not_awaited()
    archive.assert_not_awaited()
    for run_id in ["run-1", "run-2"]:
        published = [
            e.payload["source_path"]
            for e in await events.list_after("tenant-a", run_id, 0)
            if e.type == "artifact.ready"
        ]
        assert published == ["outputs/report.txt"]


@pytest.mark.asyncio
async def test_plain_chat_never_allocates_computer(database: DatabaseFixture, tmp_path: Path):
    registry, driver, sandbox = provider(database, tmp_path)
    orchestrator, _, _, _ = await arrange(tmp_path, sandbox_override=sandbox)
    result = await orchestrator.execute("tenant-a", "run-1")
    assert result.status is RunStatus.SUCCEEDED and driver.calls == []
    with pytest.raises(NotFoundError):
        await registry.get("tenant-a", "user-1")


@pytest.mark.asyncio
async def test_lost_heartbeat_cancels_runtime_and_confirms_cleanup(
    database: DatabaseFixture,
    tmp_path: Path,
):
    registry, driver, sandbox = provider(database, tmp_path, ttl=10)
    driver.fail_renew = True
    runtime = FileRuntime(hang=True)
    orchestrator, _, _, _ = await arrange(
        tmp_path, sandbox_override=sandbox, runtime_override=runtime
    )
    result = await asyncio.wait_for(orchestrator.execute("tenant-a", "run-1"), timeout=8)
    assert result.status is RunStatus.FAILED
    assert runtime.cancelled and "renew" in driver.calls and "finish" in driver.calls
    assert (await registry.get("tenant-a", "user-1")).state is ComputerState.IDLE


@pytest.mark.asyncio
async def test_stop_retains_owner_disk_and_finishes_lease_before_return(
    database: DatabaseFixture,
    tmp_path: Path,
):
    registry, driver, sandbox = provider(database, tmp_path)
    runtime = FileRuntime(hang=True)
    orchestrator, _, runs, _ = await arrange(
        tmp_path, sandbox_override=sandbox, runtime_override=runtime
    )
    task = asyncio.create_task(orchestrator.execute("tenant-a", "run-1"))
    await asyncio.wait_for(runtime.started.wait(), timeout=3)
    current = await runs.get("tenant-a", "run-1")
    cancelled = current.model_copy(
        update={"status": RunStatus.CANCELLING, "fencing_token": current.fencing_token + 1}
    )
    assert await runs.compare_and_set(current.status, cancelled)
    result = await asyncio.wait_for(task, timeout=3)
    assert result.status is RunStatus.CANCELLED
    assert runtime.cancelled and driver.calls[-1] == "finish"
    assert (driver.root / "project.txt").read_text() == "run-1"
    assert (await registry.get("tenant-a", "user-1")).state is ComputerState.IDLE


@pytest.mark.asyncio
async def test_worker_task_shutdown_drains_execution_and_resume_reuses_owner_disk(
    database: DatabaseFixture,
    tmp_path: Path,
):
    registry, driver, sandbox = provider(database, tmp_path)
    runtime = FileRuntime(hang=True)
    orchestrator, _, runs, _ = await arrange(
        tmp_path, sandbox_override=sandbox, runtime_override=runtime
    )
    task = asyncio.create_task(orchestrator.execute("tenant-a", "run-1"))
    await asyncio.wait_for(runtime.started.wait(), timeout=3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3)
    stopped = await registry.get("tenant-a", "user-1")
    assert stopped.state is ComputerState.IDLE and driver.calls[-1] == "finish"
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.RUNNING
    assert (driver.root / "project.txt").read_text() == "run-1"
    runtime.hang = False
    result = await orchestrator.execute("tenant-a", "run-1")
    recovered = await registry.get("tenant-a", "user-1")
    assert result.status is RunStatus.SUCCEEDED
    assert recovered.computer_id == stopped.computer_id
    assert recovered.provider_ref == stopped.provider_ref
    assert driver.calls.count("start") == 1 and driver.calls.count("connect") == 2
