"""Node-side QEMU computer lifecycle and its guest control transport.

The control service owns this module. Guest keys live in the node-private
identity directory; the guest accepts only its forced ``ssh_gate.py`` entry.
Lifecycle adopts an administrator-provisioned runner container and never
creates user disks; a fresh VM is explicit maintenance, not a Run side effect.
"""

import asyncio
import json
import re
from collections.abc import Mapping
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness.computer.access import ComputerAuthorizer
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    LeasePurpose,
)
from harness.computer.registry import ComputerRegistry
from harness.computer.vm_keys import VmSeedStore
from harness.sandbox.base import SandboxCommandResult

GATE_VERSION = 1
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
MAX_SPEC_BYTES = 40 * 1024 * 1024
IDENTITY_PATTERN = re.compile(r"(computer_[a-f0-9]{32})-([a-f0-9]{32})")


class VmComputerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    volume_ref: str = Field(min_length=1, max_length=128)
    container_name: str = Field(min_length=1, max_length=128)
    ssh_host: str = Field(min_length=1, max_length=253)
    ssh_port: int = Field(default=2222, ge=1, le=65535)
    runner_image: str = Field(min_length=1, max_length=512)
    guest_base_digest: str
    root_disk_source: str = Field(min_length=1, max_length=512)
    root_disk_target: str = "/vm/root.qcow2"
    work_target: str = "/data/kai-home"
    identity_dir: str = Field(min_length=1, max_length=512)
    kvm_device: str = Field(min_length=1, max_length=512)
    network: str = Field(min_length=1, max_length=128)
    adopt: bool = False
    wait_ssh_seconds: int = Field(default=240, ge=5, le=900)

    @field_validator("guest_base_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if DIGEST.fullmatch(value) is None:
            raise ValueError("VM computer image must be an immutable sha256 digest")
        return value

    def seed(self) -> VmSeedStore:
        directory = Path(self.identity_dir)
        match = IDENTITY_PATTERN.fullmatch(directory.name)
        if match is None:
            raise RuntimeError("computer VM identity directory is malformed")
        # Loads and validates the saved identity; it never regenerates keys.
        return VmSeedStore(directory.parent).prepare(match.group(1), match.group(2))


class GuestGate:
    """One guest's authenticated forced-command SSH transport.

    Every call ships one gate request on stdin; the guest never receives a
    shell, paths or commands from us. A multiplexed master keeps per-call
    latency low without persisting anything user-visible.
    """

    def __init__(self, spec: VmComputerSpec) -> None:
        self.spec = spec
        seed = spec.seed()
        self._argv = [
            "ssh",
            "-T",
            "-F",
            "/dev/null",
            "-p",
            str(spec.ssh_port),
            "-i",
            str(Path(spec.identity_dir) / "control"),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "UserKnownHostsFile=" + str(Path(spec.identity_dir) / "known_hosts"),
            "-o",
            "HostKeyAlias=" + seed.host_key_alias,
            "-o",
            "CheckHostIP=no",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=4",
            "--",
            f"kai-control@{spec.ssh_host}",
        ]

    async def call(
        self, action: str, spec: Mapping[str, object], *, timeout_seconds: float
    ) -> SandboxCommandResult:
        body = json.dumps({"version": GATE_VERSION, "action": action, "spec": dict(spec)}).encode()
        if len(body) > MAX_SPEC_BYTES:
            raise ValueError("computer guest request exceeds size limit")
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                process.communicate(body), timeout=timeout_seconds + 60
            )
        except TimeoutError:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            raise RuntimeError("computer guest control did not answer in time") from None
        code = process.returncode
        assert code is not None
        stderr = err.decode(errors="replace")
        if code == 255:
            # SSH transport errors may embed private paths; never reflect them.
            raise RuntimeError("computer guest control transport failed")
        if code == 125 and "refused" in stderr:
            raise ComputerLeaseLostError("computer guest control refused this lease")
        return SandboxCommandResult(
            exit_code=code, stdout=out.decode(errors="replace"), stderr=stderr
        )


async def wait_for_ssh(spec: VmComputerSpec, deadline_seconds: float) -> None:
    delay = 0.5
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    while True:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(spec.ssh_host, spec.ssh_port), timeout=3
            )
            try:
                banner = await asyncio.wait_for(reader.readline(), timeout=3)
            finally:
                writer.close()
            if banner.startswith(b"SSH-"):
                return
        except (OSError, TimeoutError):
            pass
        if loop.time() >= deadline:
            raise RuntimeError("computer VM did not expose its private control transport")
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 5)


def _mount_matches(mounts: list[dict[str, object]], spec: VmComputerSpec) -> bool:
    disk = any(
        entry.get("Type") == "bind"
        and entry.get("Source") == spec.root_disk_source
        and entry.get("Destination") == spec.root_disk_target
        and entry.get("RW") is True
        for entry in mounts
    )
    volume = any(
        entry.get("Type") == "volume"
        and entry.get("Name") == spec.volume_ref
        and entry.get("Destination") == spec.work_target
        and entry.get("RW") is True
        for entry in mounts
    )
    return disk and volume


def verify_vm_instance(info: dict[str, object], spec: VmComputerSpec) -> str:
    """Confirm the adopted container really carries this computer's disks.

    An unlabeled, compose-managed container is accepted only when the node
    config explicitly declares it adopted; any owner label present must match.
    """

    config = info.get("Config") or {}
    identifier = info.get("Id")
    labels = config.get("Labels") or {}
    if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{64}", identifier) is None:
        raise RuntimeError("computer VM did not return a usable instance identity")
    if config.get("Image") != spec.runner_image:
        raise RuntimeError("computer VM runner image does not match the node declaration")
    owned = labels.get("kai.computer.id")
    if owned is not None and owned != spec.volume_ref:
        raise RuntimeError("computer VM container belongs to another computer")
    if owned is None and not spec.adopt:
        raise RuntimeError("computer VM container is not labelled for this computer")
    if not _mount_matches(info.get("Mounts") or [], spec):
        raise RuntimeError("computer VM disks do not match the node declaration")
    devices = (info.get("HostConfig") or {}).get("Devices") or []
    if not any(
        entry.get("PathOnHost") == spec.kvm_device and entry.get("PathInContainer") == "/dev/kvm"
        for entry in devices
    ):
        raise RuntimeError("computer VM has no private KVM device")
    return identifier


class VmComputerControl:
    """Serves exec and lease actions for one VM computer through its gate."""

    def __init__(
        self,
        registry: ComputerRegistry,
        specs: Mapping[str, VmComputerSpec],
        *,
        authorizer: ComputerAuthorizer | None = None,
    ) -> None:
        self.registry, self.specs, self.authorizer = registry, dict(specs), authorizer

    def _spec(self, computer: AssistantComputer) -> VmComputerSpec:
        spec = self.specs.get(computer.volume_ref)
        if spec is None or spec.guest_base_digest != computer.image:
            raise RuntimeError("computer VM is not declared on this node")
        return spec

    async def call(
        self, token: ComputerLease, action: str, payload: Mapping[str, object]
    ) -> SandboxCommandResult:
        computer, current = await self.registry.holder(token)
        if computer.provider != "vm-computer":
            raise ComputerLeaseLostError("computer control purpose does not match its lease")
        purposes = (
            {LeasePurpose.START, LeasePurpose.RECOVER}
            if action == "init"
            else {LeasePurpose.EXECUTE}
        )
        if current.purpose not in purposes:
            raise ComputerLeaseLostError("computer control purpose does not match its lease")
        if self.authorizer is not None:
            await self.authorizer.authorize(computer, current, action)
        spec = self._spec(computer)
        request = {
            **payload,
            "epoch": current.epoch,
            "lease_id": current.lease_id,
            "expires_at": current.expires_at.timestamp(),
        }
        duration = payload.get("timeout_seconds", 30)
        if not isinstance(duration, (int, float)) or not 0 < duration <= 3600:
            raise ValueError("computer execution duration is invalid")
        return await GuestGate(spec).call(action, request, timeout_seconds=float(duration))


class VmComputerLifecycle:
    """Adopt, expose and fence one persistent VM per configured computer."""

    def __init__(
        self,
        registry: ComputerRegistry,
        docker: httpx.AsyncClient,
        specs: Mapping[str, VmComputerSpec],
        *,
        authorizer: ComputerAuthorizer | None = None,
    ) -> None:
        self.registry, self.docker, self.specs, self.authorizer = (
            registry,
            docker,
            dict(specs),
            authorizer,
        )

    async def check_image(self, image: str) -> None:
        if DIGEST.fullmatch(image) is None:
            raise ComputerStartRejectedError("computer VM image is not immutable")
        if all(spec.guest_base_digest != image for spec in self.specs.values()):
            raise ComputerStartRejectedError("computer VM image is not enabled on this node")

    async def lifecycle(
        self, token: ComputerLease, operation: str, *, image: str | None = None
    ) -> str | None:
        async with self.registry.host_operation(token) as (computer, current):
            if computer.provider != "vm-computer":
                raise ComputerLeaseLostError("computer is not managed by this host")
            if self.authorizer is not None:
                await self.authorizer.authorize(computer, current, operation)
            spec = self._spec(computer)
            if operation == "ensure" and current.purpose is LeasePurpose.START:
                return await self._ensure(spec)
            if operation == "reset" and current.purpose is LeasePurpose.RECOVER:
                return await self._reset(spec, token)
            raise ComputerLeaseLostError("computer lifecycle purpose does not match")

    def _spec(self, computer: AssistantComputer) -> VmComputerSpec:
        spec = self.specs.get(computer.volume_ref)
        if spec is None or spec.guest_base_digest != computer.image:
            raise RuntimeError("computer VM is not declared on this node")
        return spec

    async def _ensure(self, spec: VmComputerSpec) -> str:
        # Fresh VM creation is deliberate maintenance; a Run never builds disks.
        found = await self.docker.get(f"/containers/{spec.container_name}/json")
        if found.status_code == 404:
            raise ComputerStartRejectedError("computer VM runner is not provisioned on this node")
        found.raise_for_status()
        info = found.json()
        identifier = verify_vm_instance(info, spec)
        if info.get("State", {}).get("Running") is not True:
            started = await self.docker.post(f"/containers/{identifier}/start")
            started.raise_for_status()
        await wait_for_ssh(spec, float(spec.wait_ssh_seconds))
        return identifier

    async def _reset(self, spec: VmComputerSpec, token: ComputerLease) -> str | None:
        identifier = await self._inspect(spec)
        if identifier is None:
            return None
        # Prefer guest fencing: the recovery epoch publishes a new gate before
        # draining the old task namespace, so the VM, its browser and desktop
        # survive. Only an unreachable guest falls back to stopping the VM.
        try:
            gate = GuestGate(spec)
            request = {
                "epoch": token.epoch,
                "lease_id": token.lease_id,
                "expires_at": token.expires_at.timestamp(),
            }
            await gate.call("activate", request, timeout_seconds=30)
            await gate.call("deactivate", request, timeout_seconds=30)
            return identifier
        except (ComputerLeaseLostError, RuntimeError, ValueError, OSError):
            await self._stop(spec)
            return None

    async def _inspect(self, spec: VmComputerSpec) -> str | None:
        found = await self.docker.get(f"/containers/{spec.container_name}/json")
        if found.status_code == 404:
            return None
        found.raise_for_status()
        return verify_vm_instance(found.json(), spec)

    async def _stop(self, spec: VmComputerSpec) -> None:
        stopped = await self.docker.post(
            f"/containers/{spec.container_name}/stop", params={"t": "30"}
        )
        if stopped.status_code not in {200, 304, 404}:
            stopped.raise_for_status()
        removed = await self.docker.delete(
            f"/containers/{spec.container_name}", params={"force": "true"}
        )
        if removed.status_code not in {200, 204, 404}:
            removed.raise_for_status()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 30
        while loop.time() < deadline:
            check = await self.docker.get(f"/containers/{spec.container_name}/json")
            if check.status_code == 404:
                return
            check.raise_for_status()
            await asyncio.sleep(0.1)
        raise RuntimeError("computer VM did not stop in time; it remains fenced")
