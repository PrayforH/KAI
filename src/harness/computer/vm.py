"""Worker-side driver for persistent QEMU computers.

The protocol is the same private HTTP control plane as Docker computers; the
node resolves the provider from the canonical registry and serves VM computers
through the guest's forced SSH control gate. This process never holds guest
keys or speaks to the guest directly.
"""

import json

from harness.computer.docker import ComputerHost, DockerComputerDriver
from harness.computer.models import AssistantComputer, ComputerLease, LeasePurpose

PROVIDER = "vm-computer"


class VmComputerDriver(DockerComputerDriver):
    provider = PROVIDER
    image_office = True  # the guest base installs ~/.venvs/office

    def __init__(
        self, host: ComputerHost, *, image: str, native_desktop: bool = False,
        lazy_desktop: bool = False,
    ) -> None:
        super().__init__(host, image=image)
        self.native_desktop = native_desktop
        self.lazy_desktop = lazy_desktop

    async def connect(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self._validate(computer, token, LeasePurpose.EXECUTE)
        # A halted VM keeps its instance; waking is idempotent and is a no-op
        # probe when it is already running. Never creates disks or containers.
        await self.host.lifecycle(token, "wake")
        if token.desktop_id != "vm":
            await self._slot(token, "prepare" if self.lazy_desktop else "ensure")
        await super().connect(computer, token)

    async def ensure_desktop(self, token: ComputerLease) -> None:
        await self._slot(token, "ensure")

    async def _slot(self, token: ComputerLease, operation: str) -> None:
        payload: dict[str, object] = {"timeout_seconds": 120}
        if operation == "prepare":
            payload["op"] = operation
        result = await self.host.call(token, "desktop_session", payload)
        expected = "prepared" if operation == "prepare" else "active"
        if result.exit_code != 0 or json.loads(result.stdout) != {
            "op": operation, "state": expected,
        }:
            raise RuntimeError(f"Bot desktop did not confirm {operation}")

    async def recover(self, computer: AssistantComputer, token: ComputerLease) -> str | None:
        self._validate(computer, token, LeasePurpose.RECOVER)
        # The node fences through the guest gate when the VM is reachable and
        # reports the surviving instance; a stopped VM reports absence. Either
        # way the caller learns a confirmed state, never an optimistic guess.
        return await self.host.lifecycle(token, "reset")
