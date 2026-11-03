"""User-provided persistent computers use the same execution and desktop leases.

Lifecycle may attach or fence KAI processes; it never powers off the user's PC.
"""

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.computer.access import ComputerAuthorizer
from harness.computer.gate_ack import validate as validate_ack
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    LeasePurpose,
)
from harness.computer.registry import ComputerRegistry
from harness.computer.ssh_transport import SshEndpoint
from harness.computer.vm import VmComputerDriver
from harness.computer.vm_host import GuestGate, SshComputerControl

PROVIDER = "remote-computer"


class RemoteComputerSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    volume_ref: str = Field(min_length=1, max_length=128)
    computer_id: str = Field(pattern=r"^computer_[a-f0-9]{32}$")
    machine_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    guest_base_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    control: SshEndpoint
    viewer: SshEndpoint

    def ssh_endpoint(self, kind: Literal["control", "viewer"]) -> SshEndpoint:
        return self.control if kind == "control" else self.viewer


class RemoteComputerDriver(VmComputerDriver):
    provider = PROVIDER
    image_office = False


class RemoteComputerControl(SshComputerControl):
    provider = PROVIDER

    def _spec(self, computer: AssistantComputer) -> RemoteComputerSpec:
        spec = self.specs.get(computer.volume_ref)
        if (
            not isinstance(spec, RemoteComputerSpec)
            or spec.computer_id != computer.computer_id
            or spec.guest_base_digest != computer.image
        ):
            raise ComputerLeaseLostError("remote computer identity is not declared")
        return spec


class RemoteComputerLifecycle:
    def __init__(
        self,
        registry: ComputerRegistry,
        specs: Mapping[str, RemoteComputerSpec],
        *,
        authorizer: ComputerAuthorizer | None = None,
    ):
        self.registry, self.specs, self.authorizer = registry, dict(specs), authorizer

    async def check_image(self, image: str) -> None:
        if not any(s.guest_base_digest == image for s in self.specs.values()):
            raise ComputerStartRejectedError("remote computer component version is not enabled")

    async def lifecycle(
        self, token: ComputerLease, operation: str, *, image: str | None = None
    ) -> str:
        async with self.registry.host_operation(token) as (computer, current):
            if computer.provider != PROVIDER:
                raise ComputerLeaseLostError("computer is not managed by this remote host")
            if self.authorizer is not None:
                await self.authorizer.authorize(computer, current, operation)
            spec = self.specs.get(computer.volume_ref)
            if (
                spec is None
                or spec.computer_id != computer.computer_id
                or spec.guest_base_digest != computer.image
            ):
                raise ComputerLeaseLostError("remote computer declaration does not match")
            gate = GuestGate(spec)
            identity = await gate.call("identity", {}, timeout_seconds=10)
            expected = {
                "machine_id": spec.machine_id,
                "computer_id": spec.computer_id,
                "image": spec.guest_base_digest,
            }
            if identity.exit_code or json.loads(identity.stdout) != expected:
                raise RuntimeError("remote computer did not confirm its pinned identity")
            if operation == "ensure" and current.purpose is LeasePurpose.START:
                return "remote_" + spec.machine_id
            if operation == "reset" and current.purpose is LeasePurpose.RECOVER:
                request = {
                    "epoch": current.epoch,
                    "lease_id": current.lease_id,
                    "expires_at": current.expires_at.timestamp(),
                }
                for action, active in (("activate", True), ("deactivate", False)):
                    ack = await gate.call(action, request, timeout_seconds=30)
                    validate_ack(ack, epoch=current.epoch, lease_id=current.lease_id, active=active)
                return "remote_" + spec.machine_id
            raise ComputerLeaseLostError("remote lifecycle purpose does not match")
