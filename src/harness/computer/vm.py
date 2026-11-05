"""Worker-side driver for persistent QEMU computers.

The protocol is the same private HTTP control plane as Docker computers; the
node resolves the provider from the canonical registry and serves VM computers
through the guest's forced SSH control gate. This process never holds guest
keys or speaks to the guest directly.
"""

from harness.computer.docker import ComputerHost, DockerComputerDriver
from harness.computer.models import AssistantComputer, ComputerLease, LeasePurpose

PROVIDER = "vm-computer"


class VmComputerDriver(DockerComputerDriver):
    provider = PROVIDER
    image_office = True  # the guest base installs ~/.venvs/office

    def __init__(
        self, host: ComputerHost, *, image: str, native_desktop: bool = False,
    ) -> None:
        super().__init__(host, image=image)
        self.native_desktop = native_desktop

    async def recover(self, computer: AssistantComputer, token: ComputerLease) -> str | None:
        self._validate(computer, token, LeasePurpose.RECOVER)
        # The node fences through the guest gate when the VM is reachable and
        # reports the surviving instance; a stopped VM reports absence. Either
        # way the caller learns a confirmed state, never an optimistic guess.
        return await self.host.lifecycle(token, "reset")
