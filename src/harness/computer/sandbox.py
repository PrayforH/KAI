"""Lazy persistent Computer tools for a Worker-local model runtime.

A Run scope owns the heartbeat task group. A model-only conversation allocates
no computer; first tool use acquires the lease, captures the remote baseline,
and uploads only this Run's staged inputs. Scope failure cancels orchestration.
"""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from harness.computer.docker import WORKSPACE, DockerComputerDriver
from harness.computer.manager import ComputerAllocation, ComputerManager
from harness.computer.registry import ComputerRegistry
from harness.computer.workspace import ComputerWorkspace
from harness.core.models import Run, Session
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxEgress,
    SandboxHandle,
    SandboxIsolation,
    replace_collected_file,
)
from harness.sandbox.deferred import UnsupportedEgressError


@dataclass
class _Scope:
    tasks: asyncio.TaskGroup


@dataclass
class _Lease:
    run: Run
    handle: SandboxHandle
    scope: _Scope
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: asyncio.Future[tuple[ComputerAllocation, ComputerWorkspace]] | None = None
    task: asyncio.Task[None] | None = None


class ComputerSandboxProvider:
    def __init__(
        self,
        registry: ComputerRegistry,
        driver: DockerComputerDriver,
        session: Session,
        *,
        local_root: Path | None = None,
        max_bytes: int = 512 * 1024**2,
        max_members: int = 10_000,
    ) -> None:
        self.registry, self.driver, self.session = registry, driver, session
        self.local_root = local_root
        self.max_bytes, self.max_members = max_bytes, max_members
        self._scopes: dict[str, _Scope] = {}
        self._leases: dict[str, _Lease] = {}

    @asynccontextmanager
    async def run_scope(self, run: Run) -> AsyncGenerator[None]:
        if (
            run.tenant_id != self.session.tenant_id
            or run.session_id != self.session.session_id
            or run.run_id in self._scopes
        ):
            raise ValueError("Computer scope does not match its bound Session")
        try:
            async with asyncio.TaskGroup() as tasks:
                self._scopes[run.run_id] = _Scope(tasks)
                try:
                    yield
                finally:
                    for lease in list(self._leases.values()):
                        if lease.run.run_id == run.run_id:
                            await self.destroy(lease.handle)
        finally:
            self._scopes.pop(run.run_id, None)

    async def provision(self, run: Run) -> SandboxHandle:
        scope = self._scopes.get(run.run_id)
        if scope is None:
            raise ValueError("Computer provisioning requires a live Run scope")
        if run.tenant_id != self.session.tenant_id or run.session_id != self.session.session_id:
            raise ValueError("Computer Run identity does not match its bound Session")
        path = Path(tempfile.mkdtemp(prefix="kai-computer-run-", dir=self.local_root))
        handle = SandboxHandle(
            sandbox_id="computer-run-" + uuid4().hex,
            path=path,
            provider=self.driver.provider,
            isolation_level=SandboxIsolation.CONTAINER,
            remote_workspace=WORKSPACE,
            deferred_tool_execution=True,
            persistent_workspace=True,
        )
        self._leases[handle.sandbox_id] = _Lease(run, handle, scope)
        return handle

    async def provision_with_egress(self, run: Run, egress: SandboxEgress) -> SandboxHandle:
        if egress.is_restrictive():
            raise UnsupportedEgressError("Computer cannot enforce this Run's network policy")
        return await self.provision(run)

    def _lease(self, handle: SandboxHandle) -> _Lease:
        lease = self._leases.get(handle.sandbox_id)
        if lease is None or lease.handle != handle:
            raise ValueError("Computer handle is not active")
        return lease

    async def prepare(self, handle: SandboxHandle) -> None:
        self._lease(handle)

    async def _remote(self, handle: SandboxHandle) -> tuple[ComputerAllocation, ComputerWorkspace]:
        lease = self._lease(handle)
        async with lease.lock:
            if lease.ready is None:
                lease.ready = asyncio.get_running_loop().create_future()
                lease.task = lease.scope.tasks.create_task(self._hold(lease))
        return await asyncio.shield(lease.ready)

    async def _hold(self, lease: _Lease) -> None:
        assert lease.ready is not None
        manager = ComputerManager(self.registry, self.driver, owner=lease.handle.sandbox_id)
        try:
            async with manager.execution(
                self.session.tenant_id,
                self.session.user_id,
                run_id=lease.run.run_id,
                session_id=lease.run.session_id,
            ) as allocation:
                workspace = ComputerWorkspace(
                    self.driver, allocation, max_bytes=self.max_bytes, max_members=self.max_members
                )
                await workspace.capture_baseline()
                await workspace.stage(lease.handle.path)
                lease.ready.set_result((allocation, workspace))
                await lease.stop.wait()
        except BaseException as error:
            if not lease.ready.done():
                lease.ready.set_exception(error)
                lease.ready.exception()  # Scope can fail before the waiter resumes.
            raise

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        allocation, _ = await self._remote(handle)
        return await self.driver.execute(
            allocation, argv, environment=environment, timeout_seconds=timeout_seconds
        )

    async def upload_files(
        self, handle: SandboxHandle, entries: Sequence[tuple[str, bytes]]
    ) -> None:
        _, workspace = await self._remote(handle)
        await workspace.upload([(path, data, 0o600) for path, data in entries])

    async def download_file(self, handle: SandboxHandle, path: str, *, max_bytes: int) -> bytes:
        _, workspace = await self._remote(handle)
        content = await workspace.download(path, max_bytes=max_bytes)
        replace_collected_file(workspace.mirror_target(handle.path, path), content)
        return content

    async def collect(self, handle: SandboxHandle) -> None:
        lease = self._lease(handle)
        if lease.ready is not None:
            _, workspace = await asyncio.shield(lease.ready)
            await workspace.collect(handle.path)

    async def workspace_baseline(self, handle: SandboxHandle) -> Mapping[str, str]:
        lease = self._lease(handle)
        if lease.ready is None:
            return {}
        _, workspace = await asyncio.shield(lease.ready)
        return workspace.fingerprints()

    async def destroy(self, handle: SandboxHandle) -> None:
        lease = self._leases.pop(handle.sandbox_id, None)
        if lease is None:
            return
        try:
            lease.stop.set()
            if lease.task is not None:
                await asyncio.shield(lease.task)
        finally:
            shutil.rmtree(lease.handle.path)
