"""Lazy persistent Computer tools for a Worker-local model runtime.

A Run scope owns the heartbeat task group. A model-only conversation allocates
no computer; first tool use acquires the lease. Only file/shell/ACP use captures
the remote baseline and stages inputs; GUI observations do not scan the disk.
Scope failure cancels orchestration.
"""

import asyncio
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from harness.computer.desktop import DesktopControlService
from harness.computer.docker import WORKSPACE, DockerComputerDriver
from harness.computer.manager import ComputerAllocation, ComputerManager
from harness.computer.models import ComputerBusyError, ComputerRecoveryRequiredError
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
    workspace_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    workspace_ready: bool = False
    ready: asyncio.Future[tuple[ComputerAllocation, ComputerWorkspace]] | None = None
    task: asyncio.Task[None] | None = None


class ComputerSandboxProvider:
    _BUSY_RETRY_INITIAL_SECONDS = 0.5
    _BUSY_RETRY_MAX_SECONDS = 5.0

    def __init__(
        self,
        registry: ComputerRegistry,
        driver: DockerComputerDriver,
        session: Session,
        *,
        run_reader: Callable[[str, str], Awaitable[Run]] | None = None,
        desktop: DesktopControlService | None = None,
        local_root: Path | None = None,
        max_bytes: int = 512 * 1024**2,
        max_members: int = 10_000,
        busy_wait_seconds: float = 120.0,
        desktop_for_run: Callable[[Run], Awaitable[str | None]] | None = None,
    ) -> None:
        if busy_wait_seconds < 0:
            raise ValueError("computer busy wait cannot be negative")
        self.registry, self.driver, self.session = registry, driver, session
        self.run_reader = run_reader
        self.desktop = desktop
        self.local_root = local_root
        self.max_bytes, self.max_members = max_bytes, max_members
        self.busy_wait_seconds = busy_wait_seconds
        # None keeps the historical whole-VM lease; a resolver maps each Run to
        # its Bot desktop so parallel Bots of one VM never block each other.
        self.desktop_for_run = desktop_for_run
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

    @property
    def image_office(self) -> bool:
        """Whether this provider's computer image ships the office runtime."""

        return bool(getattr(self.driver, "image_office", False))

    def owns(self, handle: SandboxHandle) -> bool:
        lease = self._leases.get(handle.sandbox_id)
        return lease is not None and lease.handle == handle

    def _lease(self, handle: SandboxHandle) -> _Lease:
        lease = self._leases.get(handle.sandbox_id)
        if lease is None or lease.handle != handle:
            raise ValueError("Computer handle is not active")
        return lease

    async def prepare(self, handle: SandboxHandle) -> None:
        self._lease(handle)

    async def _remote(
        self, handle: SandboxHandle, *, prepare_workspace: bool = True,
    ) -> tuple[ComputerAllocation, ComputerWorkspace]:
        lease = self._lease(handle)
        async with lease.lock:
            if lease.ready is None:
                lease.ready = asyncio.get_running_loop().create_future()
                lease.task = lease.scope.tasks.create_task(self._hold(lease))
        allocation, workspace = await asyncio.shield(lease.ready)
        if prepare_workspace:
            async with lease.workspace_lock:
                if not lease.workspace_ready:
                    if workspace.baseline is None:
                        await workspace.capture_baseline()
                    await workspace.stage(handle.path)
                    lease.workspace_ready = True
        return allocation, workspace

    async def _hold(self, lease: _Lease) -> None:
        assert lease.ready is not None
        manager = ComputerManager(
            self.registry,
            self.driver,
            owner=lease.handle.sandbox_id,
        )
        try:
            current_run = (
                await self.run_reader(lease.run.tenant_id, lease.run.run_id)
                if self.run_reader
                else lease.run
            )
            if current_run.session_id != lease.run.session_id:
                raise ValueError("Computer Run identity changed before acquisition")
            await self._hold_until_released(manager, lease, current_run)
        except (ComputerBusyError, ComputerRecoveryRequiredError) as error:
            # A busy or unrecoverable computer is the caller's problem, not a
            # scope failure: the Run keeps going and the tool call reports it.
            self._fail_ready(lease, error)
        except BaseException as error:
            self._fail_ready(lease, error)
            raise

    async def _hold_until_released(
        self, manager: ComputerManager, lease: _Lease, run: Run
    ) -> None:
        """Acquire the computer, waiting out a short-lived holder."""

        ready = lease.ready
        assert ready is not None
        desktop_id = await self.desktop_for_run(run) if self.desktop_for_run else None
        deadline = asyncio.get_running_loop().time() + self.busy_wait_seconds
        delay = self._BUSY_RETRY_INITIAL_SECONDS
        while True:
            try:
                async with manager.execution(
                    self.session.tenant_id,
                    self.session.user_id,
                    run_id=lease.run.run_id,
                    session_id=lease.run.session_id,
                    run_fencing_token=run.fencing_token,
                    desktop_id=desktop_id,
                ) as allocation:
                    if not ready.done():
                        ready.set_result(
                            (
                                allocation,
                                ComputerWorkspace(
                                    self.driver,
                                    allocation,
                                    max_bytes=self.max_bytes,
                                    max_members=self.max_members,
                                ),
                            )
                        )
                    await lease.stop.wait()
                    return
            except ComputerBusyError:
                if not await self._retry_when_released(lease, deadline, delay):
                    raise
                delay = min(delay * 2, self._BUSY_RETRY_MAX_SECONDS)

    async def _retry_when_released(self, lease: _Lease, deadline: float, delay: float) -> bool:
        """Sleep before retrying a busy computer; False once waiting is over."""

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        try:
            await asyncio.wait_for(lease.stop.wait(), timeout=min(delay, remaining))
        except TimeoutError:
            return True
        return False

    @staticmethod
    def _fail_ready(lease: _Lease, error: BaseException) -> None:
        if lease.ready is not None and not lease.ready.done():
            lease.ready.set_exception(error)
            lease.ready.exception()  # Scope can fail before the waiter resumes.

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
        if lease.ready is not None and lease.workspace_ready:
            _, workspace = await asyncio.shield(lease.ready)
            await workspace.collect(handle.path)

    async def workspace_baseline(self, handle: SandboxHandle) -> Mapping[str, str]:
        lease = self._lease(handle)
        if lease.ready is None or not lease.workspace_ready:
            return {}
        _, workspace = await asyncio.shield(lease.ready)
        return workspace.fingerprints()

    async def desktop_action(
        self,
        handle: SandboxHandle,
        operation: str,
        payload: Mapping[str, object],
        *,
        expected_epoch: int,
    ) -> dict[str, object]:
        """One visible-desktop action through the node's unified control gate."""

        allocation, _ = await self._remote(handle, prepare_workspace=operation == "export")
        return await self.driver.host.desktop(
            allocation.token, operation, payload, expected_epoch=expected_epoch
        )

    async def acp_action(
        self, handle: SandboxHandle, payload: Mapping[str, object],
    ) -> SandboxCommandResult:
        allocation, _ = await self._remote(handle)
        return await self.driver.host.call(allocation.token, "acp", payload)

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
            await self._release_desktop(handle)

    async def _release_desktop(self, handle: SandboxHandle) -> None:
        # A failed release never locks the owner out: takeover overrides a
        # stale agent state, and the next Run simply cannot acquire until then.
        if self.desktop is None:
            return
        try:
            await self.desktop.release_agent(
                self.session.tenant_id, self.session.user_id, owner=handle.sandbox_id
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "computer desktop release failed", exc_info=True
            )
