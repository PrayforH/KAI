"""Node-side QEMU computer lifecycle and its guest control transport.

The control service owns this module. Guest keys live in the node-private
identity directory; the guest accepts only its forced ``ssh_gate.py`` entry.
Lifecycle adopts an administrator-provisioned runner container and never
creates user disks; a fresh VM is explicit maintenance, not a Run side effect.
"""

import asyncio
import base64
import contextlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from harness.computer.access import ComputerAuthorizer
from harness.computer.desktop_client import (
    RfbClient,
)
from harness.computer.desktop_client import (
    click as desktop_click,
)
from harness.computer.desktop_client import (
    press_keys as desktop_press,
)
from harness.computer.desktop_client import (
    scroll as desktop_scroll,
)
from harness.computer.desktop_client import (
    type_text as desktop_type,
)
from harness.computer.gate_ack import GateAckError
from harness.computer.gate_ack import validate as validate_gate_ack
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    LeasePurpose,
)
from harness.computer.registry import ComputerRegistry
from harness.computer.ssh_transport import SshComputerSpec, SshEndpoint
from harness.computer.vm_keys import ComputerVmSeed, VmSeedStore
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

    def ssh_endpoint(self, kind: Literal["control", "viewer"]) -> SshEndpoint:
        return SshEndpoint(
            host=self.ssh_host,
            port=self.ssh_port,
            user="kai-control" if kind == "control" else "kai-view",
            identity=str(Path(self.identity_dir) / kind),
            known_hosts=str(Path(self.identity_dir) / "known_hosts"),
            host_key_alias=self.seed().host_key_alias,
        )

    def seed(self) -> ComputerVmSeed:
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

    def __init__(self, spec: SshComputerSpec) -> None:
        self.spec = spec
        self._argv = spec.ssh_endpoint("control").argv()

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


def _mount_matches(mounts: list[dict[str, Any]], spec: VmComputerSpec) -> bool:
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


def verify_vm_instance(info: dict[str, Any], spec: VmComputerSpec) -> str:
    """Confirm the adopted container really carries this computer's disks.

    An unlabeled, compose-managed container is accepted only when the node
    config explicitly declares it adopted; any owner label present must match.
    """

    config: dict[str, Any] = info.get("Config") or {}
    identifier = info.get("Id")
    labels: dict[str, Any] = config.get("Labels") or {}
    if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{64}", identifier) is None:
        raise RuntimeError("computer VM did not return a usable instance identity")
    if config.get("Image") != spec.runner_image:
        raise RuntimeError("computer VM runner image does not match the node declaration")
    owned = labels.get("kai.computer.id")
    if owned is not None and owned != spec.volume_ref:
        raise RuntimeError("computer VM container belongs to another computer")
    if owned is None and not spec.adopt:
        raise RuntimeError("computer VM container is not labelled for this computer")
    mounts: list[dict[str, Any]] = info.get("Mounts") or []
    if not _mount_matches(mounts, spec):
        raise RuntimeError("computer VM disks do not match the node declaration")
    host_config: dict[str, Any] = info.get("HostConfig") or {}
    devices: list[dict[str, Any]] = host_config.get("Devices") or []
    if not any(
        entry.get("PathOnHost") == spec.kvm_device and entry.get("PathInContainer") == "/dev/kvm"
        for entry in devices
    ):
        raise RuntimeError("computer VM has no private KVM device")
    return identifier


class SshComputerControl:
    provider = "vm-computer"

    """Serves exec and lease actions for one VM computer through its gate."""

    def __init__(
        self,
        registry: ComputerRegistry,
        specs: Mapping[str, SshComputerSpec],
        *,
        authorizer: ComputerAuthorizer | None = None,
    ) -> None:
        self.registry, self.specs, self.authorizer = registry, dict(specs), authorizer

    def _spec(self, computer: AssistantComputer) -> SshComputerSpec:
        spec = self.specs.get(computer.volume_ref)
        if spec is None or spec.guest_base_digest != computer.image:
            raise RuntimeError("computer VM is not declared on this node")
        return spec

    async def call(
        self, token: ComputerLease, action: str, payload: Mapping[str, object]
    ) -> SandboxCommandResult:
        computer, current = await self.registry.holder(token)
        if computer.provider != self.provider:
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


class VmComputerControl(SshComputerControl):
    """VM specialization of the shared forced-command control transport."""


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
        # survive. The acknowledgement is the only proof the old execution is
        # fenced; an unconfirmed drain falls back to the proven physical
        # stop, and an unconfirmed stop raises so the computer stays fenced
        # instead of being handed to the next Run.
        request = {
            "epoch": token.epoch,
            "lease_id": token.lease_id,
            "expires_at": token.expires_at.timestamp(),
        }
        try:
            gate = GuestGate(spec)
            ack = await gate.call("activate", request, timeout_seconds=30)
            validate_gate_ack(ack, epoch=token.epoch, lease_id=token.lease_id, active=True)
            ack = await gate.call("deactivate", request, timeout_seconds=30)
            validate_gate_ack(ack, epoch=token.epoch, lease_id=token.lease_id, active=False)
        except (ComputerLeaseLostError, GateAckError, RuntimeError, ValueError, OSError):
            await self._stop(spec)
            return None
        return identifier

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


class DisplayTransport:
    """SSH transport to one guest's forced display gate (viewer identity).

    The RFB stream flows over the same authenticated channel the human viewer
    uses; no VNC, X11 or CDP port is ever published.
    """

    def __init__(self, spec: SshComputerSpec) -> None:
        self.banner: bytes | None = None
        self._argv = spec.ssh_endpoint("viewer").argv()

    async def open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Match the proven viewer transport: a minimal environment keeps
            # ssh from picking up unrelated proxy or agent settings.
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        assert process.stdin is not None and process.stdout is not None
        try:
            header = await asyncio.wait_for(process.stdout.readexactly(12), timeout=20)
        except (TimeoutError, asyncio.IncompleteReadError, EOFError):
            # The gate may exit before sending anything (no display service, a
            # refused sudo rule, a stale socket). Its stderr is the only clue;
            # it carries no key material, and the tail is bounded.
            detail = ""
            if process.stderr is not None:
                try:
                    raw = await asyncio.wait_for(process.stderr.read(2048), timeout=2)
                    detail = raw.decode(errors="replace").strip().splitlines()[-1][:200]
                except (TimeoutError, asyncio.IncompleteReadError):
                    detail = ""
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise RuntimeError(
                "desktop gate produced no RFB banner" + (f": {detail}" if detail else "")
            ) from None
        if not header.startswith(b"RFB 003."):
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise RuntimeError("desktop did not confirm the RFB transport")
        self.banner = header
        return process.stdout, process.stdin


GUI_OPERATIONS = frozenset({"screenshot", "move", "click", "type", "key", "scroll"})


class VmDesktopService:
    """Executes model desktop operations on one VM's visible screen.

    This is the computer's action coordinator: one lock per screen serializes
    the queue, a dequeued action re-verifies its authorization before it
    executes, and a takeover asks the in-flight input to stop between
    keystrokes. The drain acknowledgement is the busy flag clearing — only
    then may the desktop lease publish human ownership.
    """

    def __init__(self, specs: Mapping[str, SshComputerSpec]) -> None:
        self._specs = dict(specs)
        self._sessions: dict[str, RfbClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._stopping: dict[str, asyncio.Event] = {}
        self._busy: dict[str, asyncio.Event] = {}

    @property
    def served(self) -> list[str]:
        return list(self._specs)

    def _spec(self, computer: AssistantComputer) -> SshComputerSpec:
        spec = self._specs.get(computer.volume_ref)
        if spec is None:
            raise RuntimeError("computer desktop is not declared on this node")
        return spec

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _stop_event(self, key: str) -> asyncio.Event:
        event = self._stopping.get(key)
        if event is None:
            event = self._stopping[key] = asyncio.Event()
        return event

    def _busy_event(self, key: str) -> asyncio.Event:
        event = self._busy.get(key)
        if event is None:
            event = asyncio.Event()
            event.set()  # idle until an action starts
            self._busy[key] = event
        return event

    def cancel(self, volume_ref: str) -> None:
        """Ask this computer's in-flight input to stop between keystrokes."""

        if volume_ref in self._specs:
            self._stop_event(volume_ref).set()

    async def wait_drained(self, volume_ref: str, *, timeout: float) -> bool:
        """True once no action of this computer is executing (or it stopped)."""

        if volume_ref not in self._specs:
            raise RuntimeError("computer desktop is not declared on this node")
        try:
            await asyncio.wait_for(self._busy_event(volume_ref).wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def operate(
        self,
        computer: AssistantComputer,
        operation: str,
        payload: Mapping[str, object],
        *,
        revalidate: Callable[[], Awaitable[object]],
    ) -> dict[str, object]:
        if operation not in GUI_OPERATIONS:
            raise ValueError("desktop operation is not supported")
        spec = self._spec(computer)
        key = computer.volume_ref
        async with self._lock(key):
            # The queue wait may have spanned a takeover, a lease handover or
            # a recovery; a dequeued action is only executed while its
            # authorization still holds.
            await revalidate()
            client = self._sessions.get(key)
            if client is None:
                client = RfbClient(DisplayTransport(spec))
                self._sessions[key] = client
            stop = self._stop_event(key)
            stop.clear()
            busy = self._busy_event(key)
            busy.clear()
            try:
                return await self._dispatch(client, operation, payload, stop=stop)
            except (TimeoutError, RuntimeError, ValueError, OSError, EOFError):
                await client.close()
                self._sessions.pop(key, None)
                raise
            finally:
                busy.set()

    async def close(self) -> None:
        for client in self._sessions.values():
            await client.close()
        self._sessions.clear()

    async def _dispatch(
        self,
        client: RfbClient,
        operation: str,
        payload: Mapping[str, Any],
        *,
        stop: asyncio.Event | None = None,
    ) -> dict[str, object]:
        if client.width == 0:
            await client.connect()
        if operation == "screenshot":
            region = self._region(payload.get("region"))
            scale = payload.get("scale", 1.0)
            if not isinstance(scale, (int, float)) or isinstance(scale, bool):
                raise ValueError("screenshot scale must be a number")
            left, top, box_width, box_height = client.resolve_region(region)
            width, height, png = await client.screenshot(region=region, scale=float(scale))
            return {
                "width": width,
                "height": height,
                "desktop_width": client.width,
                "desktop_height": client.height,
                "region": {"x": left, "y": top, "width": box_width, "height": box_height},
                "scale": float(scale),
                "png_base64": base64.b64encode(png).decode("ascii"),
            }
        if operation in {"move", "click", "scroll"}:
            # Only pointer operations carry coordinates; typing and key presses
            # act on whatever already has focus.
            x, y = self._point(payload)
            if operation == "move":
                await client.pointer(x, y, 0)
                return {"moved": True}
            if operation == "click":
                button = str(payload.get("button", "left"))
                if button not in {"left", "middle", "right"}:
                    raise ValueError("click button is invalid")
                await desktop_click(client, x, y, button)
                return {"clicked": True}
            direction = str(payload.get("direction", ""))
            amount = payload.get("amount", 3)
            if not isinstance(amount, int) or not 1 <= amount <= 10:
                raise ValueError("scroll amount is invalid")
            await desktop_scroll(client, x, y, direction, amount)
            return {"scrolled": True}
        if operation == "type":
            text = payload.get("text")
            if not isinstance(text, str):
                raise ValueError("type requires text")
            await desktop_type(client, text, abort=None if stop is None else stop.is_set)
            return {"typed": len(text)}
        names = payload.get("keys")
        if not isinstance(names, list):
            raise ValueError("key requires a list of key names")
        # The JSON body is validated shape-wise below; the cast keeps the loop
        # elements typed instead of leaking pyright's list[Unknown] narrowing.
        keys: list[str] = [item for item in cast(list[Any], names) if isinstance(item, str)]
        if len(keys) != len(cast(list[object], names)):
            raise ValueError("key requires a list of key names")
        await desktop_press(client, keys, abort=None if stop is None else stop.is_set)
        return {"pressed": len(keys)}

    @staticmethod
    def _region(value: object) -> tuple[int, int, int, int] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("screenshot region must be an object")
        bounds = cast(Mapping[str, object], value)
        keys = ("x", "y", "width", "height")
        if any(key not in bounds for key in keys):
            raise ValueError("screenshot region needs x, y, width and height")
        numbers = [bounds[key] for key in keys]
        if any(type(item) is not int for item in numbers):
            raise ValueError("screenshot region coordinates must be integers")
        return (
            cast(int, numbers[0]),
            cast(int, numbers[1]),
            cast(int, numbers[2]),
            cast(int, numbers[3]),
        )

    @staticmethod
    def _point(payload: Mapping[str, object]) -> tuple[int, int]:
        x, y = payload.get("x"), payload.get("y")
        if type(x) is not int or type(y) is not int:
            raise ValueError("pointer coordinates must be integers")
        return x, y
