"""Per-Run egress derivation and scoping."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from harness.core.models import Run, RunStatus
from harness.sandbox.base import SandboxCommandResult, SandboxEgress, SandboxHandle
from harness.sandbox.deferred import DeferredToolSandboxProvider, UnsupportedEgressError
from harness.sandbox.egress import (
    EgressScopedSandbox,
    EgressUnsupportedError,
    sandbox_egress_policy,
)


class Tool:
    def __init__(self, *, builtin: str | None = None, mcp: str | None = None) -> None:
        self.builtin = builtin
        self.mcp = mcp


class Spec:
    def __init__(self, tools: list[Tool]) -> None:
        self.tools = tools


class Manifest:
    def __init__(self, tools: list[Tool]) -> None:
        self.spec = Spec(tools)


class Capability:
    def __init__(
        self, *, network_access: str = "internal", endpoint_url: str | None = None
    ) -> None:
        self.network_access = network_access
        self.endpoint_url = endpoint_url


def run(run_id: str = "run-a") -> Run:
    now = datetime(2026, 9, 18, tzinfo=UTC)
    return Run(
        run_id=run_id,
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.PROVISIONING,
        idempotency_key=run_id,
        created_at=now,
        updated_at=now,
    )


class RecordingBackend:
    """Backend that can express a policy."""

    def __init__(self) -> None:
        self.received: tuple[Run, SandboxEgress | None] | None = None
        self.prepared: list[str] = []
        self.destroyed: list[str] = []

    async def provision(self, run: Run) -> SandboxHandle:
        return await self.provision_with_egress(run, None)

    async def provision_with_egress(self, run: Run, egress: SandboxEgress | None) -> SandboxHandle:
        self.received = (run, egress)
        return SandboxHandle(sandbox_id="sbx-a", path=Path("/tmp/sbx-a"))

    async def prepare(self, handle: SandboxHandle) -> None:
        self.prepared.append(handle.sandbox_id)

    async def execute(self, handle: SandboxHandle, argv: Any, **kwargs: Any) -> Any:
        del handle, argv, kwargs
        return SandboxCommandResult(exit_code=0)

    async def collect(self, handle: SandboxHandle) -> None:
        del handle

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroyed.append(handle.sandbox_id)


class PlainBackend:
    """Backend that never implemented policy-aware provisioning."""

    async def provision(self, run: Run) -> SandboxHandle:
        return SandboxHandle(sandbox_id="sbx-plain", path=Path("/tmp/sbx-plain"))

    async def prepare(self, handle: SandboxHandle) -> None:
        del handle

    async def execute(self, handle: SandboxHandle, argv: Any, **kwargs: Any) -> Any:
        del handle, argv, kwargs
        return SandboxCommandResult(exit_code=0)

    async def collect(self, handle: SandboxHandle) -> None:
        del handle

    async def destroy(self, handle: SandboxHandle) -> None:
        del handle


def test_internal_mcp_becomes_a_host_allow_list() -> None:
    policy = sandbox_egress_policy(
        [Manifest([Tool(mcp="mcp-internal")])],
        {"mcp-internal": Capability(endpoint_url="https://mcp.internal.example/mcp")},
    )
    assert policy == SandboxEgress(
        allow_hosts=("mcp.internal.example",), deny_internet=True
    )


def test_agent_without_network_capabilities_gets_no_egress() -> None:
    policy = sandbox_egress_policy([Manifest([Tool(builtin="Read")])], {})
    assert policy == SandboxEgress(allow_hosts=(), deny_internet=True)
    assert policy is not None and policy.is_restrictive()


def test_web_builtin_requires_open_internet() -> None:
    assert sandbox_egress_policy([Manifest([Tool(builtin="WebSearch")])], {}) is None
    assert sandbox_egress_policy([Manifest([Tool(builtin="WebFetch")])], {}) is None


def test_external_mcp_requires_open_internet() -> None:
    assert (
        sandbox_egress_policy(
            [Manifest([Tool(mcp="mcp-external")])],
            {"mcp-external": Capability(network_access="external")},
        )
        is None
    )


def test_unresolved_mcp_reference_fails_closed() -> None:
    """Its host cannot be allowed, so the Run must be refused, not narrowed."""

    with pytest.raises(EgressUnsupportedError, match="unresolved MCP reference"):
        sandbox_egress_policy([Manifest([Tool(mcp="mcp-missing")])], {})


def test_operator_extra_hosts_are_appended() -> None:
    policy = sandbox_egress_policy(
        [Manifest([])],
        {},
        extra_hosts=("mirrors.internal.example",),
    )
    assert policy == SandboxEgress(
        allow_hosts=("mirrors.internal.example",), deny_internet=True
    )


@pytest.mark.asyncio
async def test_scoped_sandbox_injects_the_policy_for_its_run() -> None:
    backend = RecordingBackend()
    scoped = EgressScopedSandbox(backend, SandboxEgress(allow_hosts=("mcp.internal.example",)))
    handle = await scoped.provision(run())
    await scoped.prepare(handle)
    await scoped.destroy(handle)

    assert backend.received is not None
    _, egress = backend.received
    assert egress == SandboxEgress(allow_hosts=("mcp.internal.example",))
    assert backend.prepared == ["sbx-a"]
    assert backend.destroyed == ["sbx-a"]


def test_scoped_sandbox_refuses_a_backend_that_cannot_enforce() -> None:
    with pytest.raises(EgressUnsupportedError, match="cannot enforce"):
        EgressScopedSandbox(PlainBackend(), SandboxEgress(allow_hosts=("a.example",)))


@pytest.mark.asyncio
async def test_deferred_wrapper_carries_the_policy_to_a_capable_backend() -> None:
    backend = RecordingBackend()
    provider = DeferredToolSandboxProvider(backend, provider_name="cubesandbox")
    handle = await provider.provision_with_egress(
        run(), SandboxEgress(allow_hosts=("mcp.internal.example",))
    )
    try:
        # The remote sandbox is acquired on first tool use, not by prepare.
        await provider.execute(handle, ["bash", "-lc", "true"])
    finally:
        await provider.destroy(handle)

    assert backend.received is not None
    _, egress = backend.received
    assert egress == SandboxEgress(allow_hosts=("mcp.internal.example",))


@pytest.mark.asyncio
async def test_deferred_wrapper_refuses_a_backend_that_cannot_enforce() -> None:
    provider = DeferredToolSandboxProvider(PlainBackend(), provider_name="local")
    handle = await provider.provision_with_egress(
        run(), SandboxEgress(allow_hosts=("mcp.internal.example",))
    )
    try:
        with pytest.raises(UnsupportedEgressError):
            await provider.execute(handle, ["bash", "-lc", "true"])
    finally:
        await provider.destroy(handle)


@pytest.mark.asyncio
async def test_deferred_wrapper_without_a_policy_still_provisions() -> None:
    backend = RecordingBackend()
    provider = DeferredToolSandboxProvider(backend, provider_name="cubesandbox")
    handle = await provider.provision(run())
    await provider.execute(handle, ["bash", "-lc", "true"])
    await provider.destroy(handle)
    assert backend.received is not None
    _, egress = backend.received
    assert egress is None
