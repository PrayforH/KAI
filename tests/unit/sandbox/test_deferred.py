import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from datetime import UTC, datetime
from pathlib import Path

import pytest

from harness.core.models import Run, RunStatus
from harness.sandbox.base import SandboxCommandResult, SandboxHandle, SandboxIsolation
from harness.sandbox.deferred import (
    DeferredToolSandboxProvider,
    FilePlaneUnsupportedError,
)


class RecordingSandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.provisions = 0
        self.prepares = 0
        self.executions = 0
        self.collections = 0
        self.destroys = 0

    async def provision(self, run: Run) -> SandboxHandle:
        self.provisions += 1
        path = self.root / f"remote-{run.run_id}"
        path.mkdir()
        return SandboxHandle(
            sandbox_id=f"remote-{run.run_id}",
            path=path,
            provider="remote",
            isolation_level=SandboxIsolation.CONTAINER,
            remote_workspace="/workspace",
        )

    async def prepare(self, handle: SandboxHandle) -> None:
        self.prepares += 1
        assert (handle.path / "restored.txt").read_text() == "session state"

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        del environment, timeout_seconds
        self.executions += 1
        (handle.path / "generated.txt").write_text(" ".join(argv))
        return SandboxCommandResult(exit_code=0, stdout="ok")

    async def collect(self, handle: SandboxHandle) -> None:
        self.collections += 1
        assert (handle.path / "generated.txt").exists()

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroys += 1
        assert handle.sandbox_id.startswith("remote-")


def run() -> Run:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    return Run(
        run_id="run-deferred",
        session_id="session-deferred",
        tenant_id="tenant-a",
        status=RunStatus.PROVISIONING,
        idempotency_key="deferred",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_pure_model_run_never_allocates_remote_sandbox(tmp_path: Path) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="daytona",
        local_root=tmp_path,
    )

    handle = await provider.provision(run())
    await provider.prepare(handle)
    await provider.collect(handle)
    await provider.destroy(handle)

    assert handle.provider == "daytona-deferred"
    assert handle.isolation_level is SandboxIsolation.CONTAINER
    assert handle.deferred_tool_execution is True
    assert backend.provisions == 0
    assert backend.prepares == 0
    assert backend.collections == 0
    assert backend.destroys == 0


@pytest.mark.asyncio
async def test_active_run_limit_blocks_until_a_deferred_lease_is_destroyed(
    tmp_path: Path,
) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="daytona",
        local_root=tmp_path,
        max_active_runs=1,
    )
    first = await provider.provision(run())
    second_run = run().model_copy(
        update={"run_id": "run-deferred-second", "idempotency_key": "second"}
    )
    second_task = asyncio.create_task(provider.provision(second_run))
    await asyncio.sleep(0)

    assert second_task.done() is False

    await provider.destroy(first)
    second = await asyncio.wait_for(second_task, timeout=1)
    await provider.destroy(second)


def test_active_run_limit_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_active_runs must be positive"):
        DeferredToolSandboxProvider(
            RecordingSandbox(tmp_path),
            provider_name="daytona",
            local_root=tmp_path,
            max_active_runs=0,
        )


@pytest.mark.asyncio
async def test_first_tool_allocates_once_and_reuses_remote_sandbox(
    tmp_path: Path,
) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="daytona",
        local_root=tmp_path,
    )
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    first = await provider.execute(handle, ("bash", "-lc", "first"))
    second = await provider.execute(handle, ("bash", "-lc", "second"))
    await provider.collect(handle)
    await provider.destroy(handle)

    assert first.stdout == "ok"
    assert second.stdout == "ok"
    assert backend.provisions == 1
    assert backend.prepares == 1
    assert backend.executions == 2
    assert backend.collections == 1
    assert backend.destroys == 1


@pytest.mark.asyncio
async def test_read_only_tools_do_not_collect_remote_workspace(tmp_path: Path) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="daytona",
        local_root=tmp_path,
    )
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    await provider.execute(
        handle,
        ("python3", "-c", "script", "glob", '{"pattern":"**/*"}'),
    )
    await provider.execute(
        handle,
        ("python3", "-c", "script", "read", '{"file_path":"restored.txt"}'),
    )
    await provider.collect(handle)
    await provider.destroy(handle)

    assert backend.provisions == 1
    assert backend.prepares == 1
    assert backend.executions == 2
    assert backend.collections == 0
    assert backend.destroys == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "edit"])
async def test_file_mutations_collect_remote_workspace(
    tmp_path: Path,
    operation: str,
) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="daytona",
        local_root=tmp_path,
    )
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    await provider.execute(
        handle,
        ("python3", "-c", "script", operation, "{}"),
    )
    await provider.collect(handle)
    await provider.destroy(handle)

    assert backend.collections == 1


@pytest.mark.asyncio
async def test_planned_remote_workspace_is_available_before_allocation(tmp_path: Path) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="cubesandbox",
        local_root=tmp_path,
        remote_workspace_for=lambda run: f"/home/user/harness/{run.run_id}",
    )
    handle = await provider.provision(run())
    try:
        assert handle.remote_workspace == "/home/user/harness/run-deferred"
        assert backend.provisions == 0
        assert handle.deferred_tool_execution
    finally:
        await provider.destroy(handle)


class LoggingSandbox(RecordingSandbox):
    """A backend that reports its own platform log tail."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.log_reads = 0

    async def sandbox_logs(self, handle: SandboxHandle, limit: int = 40) -> tuple[str, ...]:
        del limit
        self.log_reads += 1
        return (f"log for {handle.sandbox_id}",)

    async def sandbox_metrics(self, handle: SandboxHandle) -> object:
        return {"sandbox_id": handle.sandbox_id}


@pytest.mark.asyncio
async def test_sandbox_logs_are_read_from_the_backend_once_a_tool_ran(tmp_path: Path) -> None:
    """The deployed deferred wrapper must still expose the platform's log tail.

    174 runs worker_cli_deferred, so a Run's sandbox is the wrapper's, not the
    backend's; without this passthrough a failed Run publishes no sandbox.logs
    event at all.
    """

    backend = LoggingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="cubesandbox",
        local_root=tmp_path,
    )
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    # A Run that never used a tool owns no sandbox, and reading logs must not
    # create one just to have something to report.
    assert await provider.sandbox_logs(handle) == ()
    assert backend.provisions == 0

    await provider.execute(handle, ("python3", "-c", "print('hi')"))
    assert (await provider.sandbox_logs(handle))[0].startswith("log for remote-")
    assert backend.log_reads == 1
    await provider.destroy(handle)


@pytest.mark.asyncio
async def test_sandbox_metrics_are_read_from_the_backend(tmp_path: Path) -> None:
    backend = LoggingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="cubesandbox",
        local_root=tmp_path,
    )
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")
    assert await provider.sandbox_metrics(handle) is None
    await provider.execute(handle, ("python3", "-c", "print('hi')"))
    assert await provider.sandbox_metrics(handle) == {"sandbox_id": "remote-run-deferred"}
    await provider.destroy(handle)


@pytest.mark.asyncio
async def test_sandbox_logs_are_empty_when_the_backend_has_no_log_plane(
    tmp_path: Path,
) -> None:
    """A backend without a log plane is skipped rather than failing the Run."""

    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(
        backend,
        provider_name="daytona",
        local_root=tmp_path,
    )
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")
    await provider.execute(handle, ("python3", "-c", "print('hi')"))
    assert await provider.sandbox_logs(handle) == ()
    assert await provider.sandbox_metrics(handle) is None
    await provider.destroy(handle)


class FilePlaneSandbox(RecordingSandbox):
    """A backend that can move bytes without the command plane."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.uploaded: list[list[tuple[str, bytes]]] = []
        self.read: list[str] = []

    async def collect(self, handle: SandboxHandle) -> None:
        # This backend's collection is counted, not asserted on: the case under
        # test is whether the file plane made the workspace dirty.
        del handle
        self.collections += 1

    async def upload_files(self, handle: SandboxHandle, entries: Sequence[Any]) -> None:
        self.uploaded.append(list(entries))

    async def download_file(self, handle: SandboxHandle, path: str, *, max_bytes: int) -> bytes:
        self.read.append(path)
        return b"remote"


@pytest.mark.asyncio
async def test_a_file_plane_write_marks_the_workspace_dirty(tmp_path: Path) -> None:
    """A write through the file plane is what makes collect synchronize.

    The shape heuristic exists because a command is opaque; a file-plane write is
    not, so it is recorded where it cannot be missed.
    """

    backend = FilePlaneSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(backend, provider_name="fileplane")
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    await provider.upload_files(handle, [("outputs/report.md", b"report")])
    await provider.collect(handle)

    assert backend.uploaded == [[("outputs/report.md", b"report")]]
    assert backend.collections == 1
    await provider.destroy(handle)


@pytest.mark.asyncio
async def test_a_read_through_the_file_plane_does_not_synchronize(tmp_path: Path) -> None:
    backend = FilePlaneSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(backend, provider_name="fileplane")
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    assert await provider.download_file(handle, "outputs/report.md", max_bytes=1024) == b"remote"
    await provider.collect(handle)

    assert backend.read == ["outputs/report.md"]
    assert backend.collections == 0
    await provider.destroy(handle)


@pytest.mark.asyncio
async def test_a_command_only_backend_refuses_the_file_plane(tmp_path: Path) -> None:
    backend = RecordingSandbox(tmp_path)
    provider = DeferredToolSandboxProvider(backend, provider_name="commands")
    handle = await provider.provision(run())
    (handle.path / "restored.txt").write_text("session state")

    with pytest.raises(FilePlaneUnsupportedError, match="no file plane"):
        await provider.upload_files(handle, [("outputs/report.md", b"report")])

    await provider.destroy(handle)
