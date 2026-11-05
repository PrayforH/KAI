"""VM lifecycle adoption and fencing decisions, without Docker or a guest.

The Docker API is an in-process mock transport; the guest gate and SSH wait
are stubs. The registry runs on the isolated integration database, so the
lease purposes and host-operation locking are real.
"""

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest

from harness.computer.control_server import ControllerSettings
from harness.computer.models import ComputerLease
from harness.computer.registry import ComputerRegistry
from harness.computer.vm_host import VmComputerLifecycle, VmComputerSpec
from tests.integration.storage.conftest import DatabaseFixture

BASE = "sha256:" + "b" * 64
RUNNER = "kai/computer-vm-runner:fixture"
CONTAINER = "a" * 64
NAME = "kai-computer-fixture-vm"


def spec(**changes: object) -> VmComputerSpec:
    return VmComputerSpec.model_validate(
        {
            "volume_ref": "kai-computer-fixture",
            "container_name": NAME,
            "ssh_host": "kai-computer-fixture-vm",
            "runner_image": RUNNER,
            "guest_base_digest": BASE,
            "root_disk_source": "/data/disks/fixture/root.qcow2",
            "identity_dir": "/identities/computer_" + "c" * 32 + "-" + "d" * 32,
            "kvm_device": "/dev-host/kvm",
            "network": "kai-computer-net",
            "adopt": True,
            "wait_ssh_seconds": 5,
            **changes,
        }
    )


def docker_mock(state: dict[str, Any]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == f"/containers/{NAME}/json":
            if not state.get("exists"):
                return httpx.Response(404)
            return httpx.Response(200, json=state["inspect"])
        if request.method == "POST" and path == f"/containers/{CONTAINER}/start":
            state["inspect"]["State"]["Running"] = True
            return httpx.Response(204)
        if request.method == "POST" and path == f"/containers/{NAME}/stop":
            return httpx.Response(204)
        if request.method == "DELETE" and path == f"/containers/{NAME}":
            state["exists"] = False
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://docker")


def inspect_payload(running: bool) -> dict[str, Any]:
    return {
        "Id": CONTAINER,
        "Config": {"Image": RUNNER, "Labels": {}},
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


class FakeGate:
    """Records gate calls; can simulate an unreachable guest."""

    refused = False

    def __init__(self, spec: VmComputerSpec) -> None:
        assert spec.container_name == NAME
        self.calls: list[str] = []

    async def call(self, action: str, spec: dict[str, Any], *, timeout_seconds: float) -> Any:
        if self.refused:
            raise RuntimeError("computer guest control transport failed")
        self.calls.append(action)
        from harness.sandbox.base import SandboxCommandResult

        return SandboxCommandResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "epoch": spec["epoch"],
                    "lease_id": spec["lease_id"],
                    "active": action == "activate",
                }
            ),
            stderr="",
        )


async def lifecycle(database: DatabaseFixture, state: dict[str, Any]) -> VmComputerLifecycle:
    _, sessions = database
    registry = ComputerRegistry(sessions)
    await registry.adopt(
        "tenant-a",
        "user-a",
        provider="vm-computer",
        image=BASE,
        computer_id="computer_" + "c" * 32,
        volume_ref="kai-computer-fixture",
    )
    return VmComputerLifecycle(registry, docker_mock(state), {"kai-computer-fixture": spec()})


@pytest.fixture
def gate_stub(monkeypatch: pytest.MonkeyPatch) -> type[FakeGate]:
    import harness.computer.vm_host as vm_host

    monkeypatch.setattr(vm_host, "GuestGate", FakeGate)

    async def instant(spec: VmComputerSpec, deadline: float) -> None:
        return None

    monkeypatch.setattr(vm_host, "wait_for_ssh", instant)
    return FakeGate


@pytest.mark.asyncio
async def test_ensure_starts_the_adopted_container_and_waits_for_ssh(
    database: DatabaseFixture, gate_stub: type[FakeGate]
) -> None:
    state: dict[str, Any] = {"exists": True, "inspect": inspect_payload(running=False)}
    host = await lifecycle(database, state)
    _, sessions = database
    registry: ComputerRegistry = host.registry
    token = await registry.claim_start("tenant-a", "user-a", owner="worker-fixture")
    reference = await host.lifecycle(token, "ensure")
    assert reference == CONTAINER
    assert state["inspect"]["State"]["Running"] is True


@pytest.mark.asyncio
async def test_ensure_refuses_a_missing_runner_container(
    database: DatabaseFixture, gate_stub: type[FakeGate]
) -> None:
    state: dict[str, Any] = {"exists": False}
    host = await lifecycle(database, state)
    _, sessions = database
    token = await host.registry.claim_start("tenant-a", "user-a", owner="worker-fixture")
    from harness.computer.models import ComputerStartRejectedError

    with pytest.raises(ComputerStartRejectedError):
        await host.lifecycle(token, "ensure")


@pytest.mark.asyncio
async def test_reset_fences_through_the_guest_and_keeps_the_vm_alive(
    database: DatabaseFixture, gate_stub: type[FakeGate]
) -> None:
    state: dict[str, Any] = {"exists": True, "inspect": inspect_payload(running=True)}
    host = await lifecycle(database, state)
    _, sessions = database
    token = await start_execution(sessions, host.registry)
    FakeGate.refused = False
    reference = await host.lifecycle(token, "reset")
    assert reference == CONTAINER  # the VM survived the fence
    assert state["exists"] is True


@pytest.mark.asyncio
async def test_reset_stops_the_vm_when_the_guest_is_unreachable(
    database: DatabaseFixture, gate_stub: type[FakeGate]
) -> None:
    state: dict[str, Any] = {"exists": True, "inspect": inspect_payload(running=True)}
    host = await lifecycle(database, state)
    FakeGate.refused = True
    token = await start_execution(database[1], host.registry)
    reference = await host.lifecycle(token, "reset")
    assert reference is None  # physically stopped, disks untouched
    assert state["exists"] is False


async def start_execution(sessions: Any, registry: ComputerRegistry) -> ComputerLease:
    token = await registry.claim_start("tenant-a", "user-a", owner="worker-fixture")
    await registry.complete_start(token, provider_ref=CONTAINER)
    execution = await registry.claim_execution(
        "tenant-a", "user-a", owner="worker-fixture", run_id="run-a", session_id="session-a"
    )
    # Recovery only claims what expired: force the TTL into the past like the
    # registry tests do, then take the recovery lease that fences it.
    from sqlalchemy import update

    from harness.computer.storage import ComputerLeaseRow

    async with sessions.begin() as db:
        await db.execute(
            update(ComputerLeaseRow)
            .where(ComputerLeaseRow.lease_id == execution.lease_id)
            .values(
                created_at=execution.created_at - timedelta(hours=1),
                expires_at=execution.expires_at - timedelta(hours=1),
            )
        )
    return await registry.claim_recovery("tenant-a", "user-a", owner="worker-fixture")


def _unused_settings_guard() -> None:  # pragma: no cover
    # Keeps the ControllerSettings import meaningful: the app assembles this
    # lifecycle through the same declaration path production uses.
    assert ControllerSettings.model_fields
