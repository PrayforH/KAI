"""Cloud VM power saving: halt in place, keep disks, wake on first use."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from harness.computer.manager import ComputerManager
from harness.computer.models import ComputerLease, ComputerState, LeasePurpose, LeaseState
from harness.computer.vm import VmComputerDriver
from harness.computer.vm_host import VmComputerLifecycle, VmComputerSpec
from harness.sandbox.base import SandboxCommandResult

BASE = "sha256:" + "b" * 64
NAME = "kai-computer-fixture-vm"
CONTAINER = "a" * 64


def spec() -> VmComputerSpec:
    return VmComputerSpec.model_validate(
        {
            "volume_ref": "kai-computer-fixture",
            "container_name": NAME,
            "ssh_host": NAME,
            "runner_image": "kai/computer-vm-runner:fixture",
            "guest_base_digest": BASE,
            "root_disk_source": "/data/disks/fixture/root.qcow2",
            "identity_dir": "/identities/computer_" + "c" * 32 + "-" + "d" * 32,
            "kvm_device": "/dev-host/kvm",
            "network": "kai-computer-net",
            "adopt": True,
            "wait_ssh_seconds": 5,
        }
    )


def inspect_payload(running: bool) -> dict[str, Any]:
    return {
        "Id": CONTAINER,
        "Config": {"Image": "kai/computer-vm-runner:fixture", "Labels": {}},
        "State": {"Running": running},
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/data/disks/fixture/root.qcow2",
                "Destination": "/vm/root.qcow2",
                "RW": True,
            },
            {
                "Type": "volume",
                "Name": "kai-computer-fixture",
                "Destination": "/data/kai-home",
                "RW": True,
            },
        ],
        "HostConfig": {"Devices": [{"PathOnHost": "/dev-host/kvm", "PathInContainer": "/dev/kvm"}]},
    }


class DockerFixture:
    """Docker transport that records whether it ever saw a destructive call."""

    def __init__(self, running: bool = True) -> None:
        self.state: dict[str, Any] = {"exists": True, "inspect": inspect_payload(running)}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "GET" and path == f"/containers/{NAME}/json":
            if not self.state["exists"]:
                return httpx.Response(404)
            return httpx.Response(200, json=self.state["inspect"])
        if request.method == "POST" and path == f"/containers/{NAME}/stop":
            self.state["inspect"]["State"]["Running"] = False
            return httpx.Response(204)
        if request.method == "POST" and path == f"/containers/{CONTAINER}/start":
            self.state["inspect"]["State"]["Running"] = True
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url="http://docker"
        )


class RegistryFixture:
    """Row-lock stand-in: yields one idle computer and records the halt."""

    def __init__(self) -> None:
        self.row = SimpleNamespace(
            provider="vm-computer",
            state=ComputerState.IDLE.value,
            provider_ref=CONTAINER,
            volume_ref="kai-computer-fixture",
            image=BASE,
            revision=3,
            updated_at=datetime.now(UTC),
        )
        self.gate_calls: list[dict[str, object]] = []
        self.suspended: list[object] = []

    @asynccontextmanager
    async def suspend_gate(self, tenant: str, user: str, *, provider: str, idle_seconds: float):
        self.gate_calls.append(
            {"tenant": tenant, "user": user, "provider": provider, "idle": idle_seconds}
        )
        yield self.row, datetime.now(UTC)

    def mark_suspended(self, computer: object, moment: datetime) -> None:
        self.suspended.append(computer)
        computer.state = ComputerState.SUSPENDED.value  # type: ignore[attr-defined]


async def suspend_host(
    running: bool = True,
) -> tuple[VmComputerLifecycle, RegistryFixture, DockerFixture]:
    registry, docker = RegistryFixture(), DockerFixture(running)
    host = VmComputerLifecycle(registry, docker.client(), {"kai-computer-fixture": spec()})  # type: ignore[arg-type]
    return host, registry, docker


@pytest.mark.asyncio
async def test_idle_vm_is_halted_in_place_and_keeps_its_container():
    host, registry, docker = await suspend_host()
    try:
        reference = await host.suspend_idle("tenant-a", "user-a", idle_seconds=1800)
    finally:
        await host.docker.aclose()
    assert reference == CONTAINER
    assert docker.state["inspect"]["State"]["Running"] is False
    assert docker.state["exists"] is True
    assert any(method == "POST" and path.endswith("/stop") for method, path in docker.calls)
    assert not any(method == "DELETE" for method, _ in docker.calls)
    assert registry.row.state == ComputerState.SUSPENDED.value
    assert registry.gate_calls == [
        {"tenant": "tenant-a", "user": "user-a", "provider": "vm-computer", "idle": 1800}
    ]


@pytest.mark.asyncio
async def test_already_stopped_vm_is_not_stopped_again():
    host, registry, docker = await suspend_host(running=False)
    try:
        await host.suspend_idle("tenant-a", "user-a", idle_seconds=1800)
    finally:
        await host.docker.aclose()
    assert not any(method == "POST" for method, _ in docker.calls)
    assert registry.row.state == ComputerState.SUSPENDED.value


@pytest.mark.asyncio
async def test_missing_runner_leaves_the_state_untouched():
    host, registry, docker = await suspend_host()
    docker.state["exists"] = False
    try:
        with pytest.raises(RuntimeError, match="not provisioned"):
            await host.suspend_idle("tenant-a", "user-a", idle_seconds=1800)
    finally:
        await host.docker.aclose()
    assert registry.suspended == []
    assert registry.row.state == ComputerState.IDLE.value


@pytest.mark.asyncio
async def test_wake_restarts_a_halted_runner_and_waits_for_it(monkeypatch: pytest.MonkeyPatch):
    import harness.computer.vm_host as vm_host

    waited: list[float] = []

    async def record(spec: VmComputerSpec, deadline: float) -> None:
        waited.append(deadline)

    monkeypatch.setattr(vm_host, "wait_for_ssh", record)
    host, _, docker = await suspend_host(running=False)
    try:
        assert await host._ensure(spec()) == CONTAINER
    finally:
        await host.docker.aclose()
    assert docker.state["inspect"]["State"]["Running"] is True
    assert waited == [5]


@pytest.mark.asyncio
async def test_wake_of_a_running_runner_skips_the_start_and_probe(
    monkeypatch: pytest.MonkeyPatch,
):
    import harness.computer.vm_host as vm_host

    async def refuse(spec: VmComputerSpec, deadline: float) -> None:
        raise AssertionError("a running VM must not be probed on the wake path")

    monkeypatch.setattr(vm_host, "wait_for_ssh", refuse)
    host, _, docker = await suspend_host(running=True)
    try:
        assert await host._ensure(spec()) == CONTAINER
    finally:
        await host.docker.aclose()
    assert not any(method == "POST" for method, _ in docker.calls)


def execution_lease() -> ComputerLease:
    moment = datetime.now(UTC)
    return ComputerLease(
        lease_id="computer_lease_" + "e" * 32,
        computer_id="computer_" + "c" * 32,
        tenant_id="tenant-a",
        user_id="user-a",
        owner="worker-fixture",
        purpose=LeasePurpose.EXECUTE,
        epoch=4,
        run_id="run-a",
        session_id="session-a",
        state=LeaseState.ACTIVE,
        created_at=moment,
        renewed_at=moment,
        expires_at=moment + timedelta(seconds=120),
        finished_at=None,
    )


class WakeHost:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def lifecycle(self, token: ComputerLease, operation: str, *, image: str | None = None):
        self.calls.append(operation)
        return CONTAINER

    async def call(self, token: ComputerLease, action: str, payload: object):
        self.calls.append(action)
        return SandboxCommandResult(
            exit_code=0,
            stdout=json.dumps(
                {"epoch": token.epoch, "lease_id": token.lease_id, "active": action == "activate"}
            ),
            stderr="",
        )


@pytest.mark.asyncio
async def test_execution_connect_wakes_before_activating_the_guest():
    token = execution_lease()
    computer = SimpleNamespace(
        computer_id=token.computer_id, tenant_id=token.tenant_id, user_id=token.user_id
    )
    host = WakeHost()
    driver = VmComputerDriver(host, image=BASE)  # type: ignore[arg-type]
    await driver.connect(computer, token)  # type: ignore[arg-type]
    assert host.calls == ["wake", "activate"]


@pytest.mark.asyncio
async def test_manager_treats_a_suspended_vm_as_ready_without_recovery():
    row = SimpleNamespace(
        state=ComputerState.SUSPENDED.value,
        provider="vm-computer",
        image=BASE,
        provider_ref=CONTAINER,
    )

    class ReadyRegistry:
        lease_ttl_seconds = 120.0

        async def ensure(self, tenant: str, user: str, *, provider: str, image: str):
            return row

        async def claim_start(self, *args: object, **kwargs: object):
            raise AssertionError("a suspended VM must wake through its execution lease")

        async def claim_recovery(self, *args: object, **kwargs: object):
            raise AssertionError("a suspended VM is not a recovery case")

    driver = SimpleNamespace(provider="vm-computer", image=BASE)
    manager = ComputerManager(ReadyRegistry(), driver, owner="worker-fixture")  # type: ignore[arg-type]
    assert await manager.ready("tenant-a", "user-a") is row


def power_settings() -> Any:
    from pydantic import SecretStr

    from harness.config import Settings

    return Settings.model_validate(
        {
            "assistant_computer_owners": {"tenant-a": ["user-a"]},
            "assistant_computer_control_url": "http://internal-node:8840",
            "assistant_computer_control_secret": SecretStr("private-fixture-" * 4),
            "assistant_computer_image": "sha256:" + "a" * 64,
            "assistant_computer_vm_image": BASE,
            "assistant_computer_default_provider": "vm-computer",
            "sandbox_execution_mode": "worker_cli_deferred",
        }
    )


class CandidateRegistry:
    def __init__(self, candidates: tuple[object, ...]) -> None:
        self.candidates = candidates
        self.calls: list[float] = []

    async def vm_idle_candidates(self, *, idle_seconds: float):
        self.calls.append(idle_seconds)
        return self.candidates


async def runtime_with_node(handler: Any):
    from harness.computer.runtime import ComputerRuntime
    from harness.storage.database import create_database

    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    runtime = ComputerRuntime(power_settings(), sessions, execution_enabled=True)
    assert runtime.client is not None
    await runtime.client.aclose()
    runtime.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://internal-node:8840"
    )
    return runtime, engine


@pytest.mark.asyncio
async def test_idle_vm_sweep_asks_the_node_and_counts_only_confirmed_halts():
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"state": "stopped", "provider_ref": CONTAINER})

    runtime, engine = await runtime_with_node(handler)
    runtime.registry = CandidateRegistry(
        (
            SimpleNamespace(
                tenant_id="tenant-a", user_id="user-a", provider="vm-computer"
            ),
            # A remote computer can never be swept by this window.
            SimpleNamespace(tenant_id="tenant-a", user_id="user-b", provider="remote-computer"),
        )
    )  # type: ignore[assignment]
    try:
        assert await runtime.suspend_idle_computers(idle_seconds=1800) == 1
        assert requests == [
            {"tenant_id": "tenant-a", "user_id": "user-a", "idle_seconds": 1800}
        ]
    finally:
        await runtime.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_idle_vm_sweep_skips_when_disabled_or_refused():
    runtime, engine = await runtime_with_node(
        lambda request: httpx.Response(409, json={"detail": "busy"})
    )
    registry = CandidateRegistry(
        (SimpleNamespace(tenant_id="t", user_id="u", provider="vm-computer"),)
    )
    runtime.registry = registry  # type: ignore[assignment]
    try:
        assert await runtime.suspend_idle_computers(idle_seconds=0) == 0
        assert registry.calls == []  # disabled: no listing, no request
        assert await runtime.suspend_idle_computers(idle_seconds=1800) == 0
    finally:
        await runtime.close()
        await engine.dispose()


def test_vm_idle_default_is_thirty_minutes_and_zero_disables():
    assert power_settings().assistant_computer_vm_idle_seconds == 1800
    disabled = power_settings().model_copy(update={"assistant_computer_vm_idle_seconds": 0.0})
    assert disabled.assistant_computer_vm_idle_seconds == 0

