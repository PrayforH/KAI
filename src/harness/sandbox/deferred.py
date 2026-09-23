"""Defer remote Sandbox allocation until an isolated tool actually runs."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from harness.core.models import Run
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxEgress,
    SandboxFilePlaneProvider,
    SandboxHandle,
    SandboxIsolation,
    SandboxProvider,
    SandboxResourceUsage,
)


@dataclass
class _DeferredLease:
    run: Run
    remote: SandboxHandle | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    workspace_may_be_dirty: bool = False
    # Kept on the lease, not the provider: the provider is shared by every Run
    # while each Run declares its own egress requirement.
    egress: SandboxEgress | None = None


class UnsupportedEgressError(RuntimeError):
    """Raised when a Run's egress requirement cannot be enforced by its backend."""


class FilePlaneUnsupportedError(RuntimeError):
    """Raised when a caller asks for the file plane of a command-only backend."""


# Remote file tools run as `python3 -c <script> <operation> <payload>`; the
# operation is the only part of a command this provider can classify, so a
# command whose third argument is one of these cannot have changed a file.
_FILE_OPERATION_ARGV_INDEX = 3
_READ_ONLY_FILE_OPERATIONS = frozenset({"read", "glob", "grep"})


class DeferredToolSandboxProvider:
    """Run the model locally and acquire the configured Sandbox on first tool use.

    The local path remains the authoritative Run workspace. When a remote Sandbox
    is first needed, the backend uploads that path during ``prepare``; later
    ``collect`` calls synchronize generated files back into the same path.
    """

    def __init__(
        self,
        backend: SandboxProvider,
        *,
        provider_name: str,
        local_root: Path | None = None,
        max_active_runs: int = 2,
        remote_workspace_for: Callable[[Run], str] | None = None,
    ) -> None:
        if not provider_name.strip():
            raise ValueError("deferred sandbox provider name must be non-empty")
        if max_active_runs < 1:
            raise ValueError("deferred sandbox max_active_runs must be positive")
        self._remote_workspace_for = remote_workspace_for
        self._backend = backend
        self._provider_name = provider_name
        self._local_root = local_root
        self._active_run_slots = asyncio.BoundedSemaphore(max_active_runs)
        if local_root is not None:
            local_root.mkdir(parents=True, exist_ok=True)
        self._leases: dict[str, _DeferredLease] = {}

    async def provision(self, run: Run) -> SandboxHandle:
        return await self.provision_with_egress(run, None)

    async def provision_with_egress(
        self, run: Run, egress: SandboxEgress | None
    ) -> SandboxHandle:
        """Provision carrying the Run's egress requirement to the backend.

        The sandbox is only acquired on first tool use, so the policy has to
        travel with the lease until then.
        """

        await self._active_run_slots.acquire()
        try:
            path = Path(tempfile.mkdtemp(prefix=f"{run.run_id}-deferred-", dir=self._local_root))
        except BaseException:
            self._active_run_slots.release()
            raise
        sandbox_id = path.name
        self._leases[sandbox_id] = _DeferredLease(run=run, egress=egress)
        return SandboxHandle(
            sandbox_id=sandbox_id,
            path=path,
            provider=f"{self._provider_name}-deferred",
            isolation_level=SandboxIsolation.CONTAINER,
            deferred_tool_execution=True,
            remote_workspace=self._remote_workspace_for(run)
            if self._remote_workspace_for
            else None,
        )

    async def _provision_backend(self, lease: _DeferredLease) -> SandboxHandle:
        """Hand the lease's egress requirement to the backend that can apply it."""

        applies = getattr(self._backend, "provision_with_egress", None)
        if lease.egress is None or not lease.egress.is_restrictive():
            return await self._backend.provision(lease.run)
        if applies is None:
            raise UnsupportedEgressError(
                f"{self._provider_name} cannot enforce a sandbox egress policy; "
                "refusing rather than running with unrestricted egress"
            )
        return await applies(lease.run, lease.egress)

    async def prepare(self, handle: SandboxHandle) -> None:
        self._lease(handle)

    def _lease(self, handle: SandboxHandle) -> _DeferredLease:
        try:
            return self._leases[handle.sandbox_id]
        except KeyError as error:
            raise ValueError("deferred sandbox handle is not active") from error

    async def _ensure_remote(self, handle: SandboxHandle) -> SandboxHandle:
        lease = self._lease(handle)
        if lease.remote is not None:
            return lease.remote
        async with lease.lock:
            if lease.remote is not None:
                return lease.remote
            provisioned: SandboxHandle | None = None
            try:
                provisioned = await self._provision_backend(lease)
                original_path = provisioned.path
                remote = provisioned.model_copy(
                    update={"path": handle.path, "deferred_tool_execution": True}
                )
                if original_path != handle.path:
                    shutil.rmtree(original_path, ignore_errors=True)
                await self._backend.prepare(remote)
            except BaseException:
                if provisioned is not None:
                    await self._backend.destroy(provisioned)
                raise
            lease.remote = remote
            return remote

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        remote = await self._ensure_remote(handle)
        if self._may_mutate_workspace(argv):
            # Bash can change files before exiting non-zero or timing out, so mark
            # the lease before execution and collect conservatively afterwards.
            self._lease(handle).workspace_may_be_dirty = True
        return await self._backend.execute(
            remote,
            argv,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _may_mutate_workspace(argv: Sequence[str]) -> bool:
        """Conservatively decide whether a command can change workspace files.

        Only the platform's own read-only remote file operations are provably
        safe to skip. A runtime that drives the Sandbox through a shell command
        string (``bash -lc "<anything>"``) hands the provider an opaque command
        that can mutate anything, and so does a Bundle Python operator, so the
        default answer is "assume it wrote something". The cost of a needless
        synchronization is bounded by the workspace size; the cost of a missed
        one is a file the platform never sees.
        """

        if (
            len(argv) > _FILE_OPERATION_ARGV_INDEX
            and argv[0] == "python3"
            and argv[1] == "-c"
            and argv[_FILE_OPERATION_ARGV_INDEX] in _READ_ONLY_FILE_OPERATIONS
        ):
            return False
        return True

    async def collect(self, handle: SandboxHandle) -> None:
        lease = self._lease(handle)
        if lease.remote is not None and lease.workspace_may_be_dirty:
            await self._backend.collect(lease.remote)

    async def upload_files(
        self, handle: SandboxHandle, entries: Sequence[tuple[str, bytes]]
    ) -> None:
        """Write workspace-relative files through the backend's own file API.

        A write through the file plane is what makes the workspace dirty, so the
        lease is marked here exactly as it is for a command that may have written
        something — collection no longer has to read that from a command's shape.
        """

        remote = await self._ensure_remote(handle)
        plane = self._file_plane()
        await plane.upload_files(remote, entries)
        self._lease(handle).workspace_may_be_dirty = True

    async def download_file(self, handle: SandboxHandle, path: str, *, max_bytes: int) -> bytes:
        remote = await self._ensure_remote(handle)
        return await self._file_plane().download_file(remote, path, max_bytes=max_bytes)

    def _file_plane(self) -> SandboxFilePlaneProvider:
        plane = getattr(self._backend, "upload_files", None), getattr(
            self._backend, "download_file", None
        )
        if any(member is None for member in plane):
            raise FilePlaneUnsupportedError(
                f"{self._provider_name} has no file plane; use the command proxy"
            )
        return cast(SandboxFilePlaneProvider, self._backend)

    async def destroy(self, handle: SandboxHandle) -> None:
        lease = self._leases.pop(handle.sandbox_id, None)
        try:
            if lease is not None and lease.remote is not None:
                await self._backend.destroy(lease.remote)
        finally:
            shutil.rmtree(handle.path, ignore_errors=True)
            if lease is not None:
                self._active_run_slots.release()

    async def sandbox_logs(self, handle: SandboxHandle, limit: int = 40) -> tuple[str, ...]:
        """Read the platform's log tail for a Run that actually reached a sandbox.

        A Run that never used a tool owns no backend sandbox, and reading
        diagnostics must never be the reason one gets created — so the lease is
        read as it stands and an absent remote reports no logs.
        """

        lease = self._leases.get(handle.sandbox_id)
        reader = getattr(self._backend, "sandbox_logs", None)
        if lease is None or lease.remote is None or reader is None:
            return ()
        return await reader(lease.remote, limit)

    async def sandbox_metrics(self, handle: SandboxHandle) -> SandboxResourceUsage | None:
        lease = self._leases.get(handle.sandbox_id)
        reader = getattr(self._backend, "sandbox_metrics", None)
        if lease is None or lease.remote is None or reader is None:
            return None
        return await reader(lease.remote)
