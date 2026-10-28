"""Persistent computers through the authenticated host control service.

There is no privileged execd endpoint inside these computers. Lifecycle uses
one deterministic Docker name per registered computer, so a delayed create
cannot mount the work disk in a second instance. Control always resolves the
current registry holder independently of this client's cached allocation.
"""

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol

from harness.computer import gate_ack
from harness.computer.control import ComputerControl, ControlAction
from harness.computer.manager import ComputerAllocation
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    LeasePurpose,
)
from harness.sandbox.base import SandboxCommandResult

WORKSPACE = "/home/kai/workspace"


class ComputerHost(ComputerControl, Protocol):
    async def lifecycle(
        self, token: ComputerLease, operation: str, *, image: str | None = None
    ) -> str | None: ...

    async def check_image(self, image: str, *, provider: str | None = None) -> None: ...

    async def desktop(
        self,
        token: ComputerLease,
        operation: str,
        payload: Mapping[str, object],
        *,
        expected_epoch: int,
    ) -> dict[str, object]: ...


class DockerComputerDriver:
    provider = "docker-computer"

    def __init__(self, host: ComputerHost, *, image: str) -> None:
        if not image.strip():
            raise ValueError("computer driver requires an image")
        self.host, self.image = host, image

    @staticmethod
    def _validate(computer: AssistantComputer, token: ComputerLease, purpose: LeasePurpose) -> None:
        if (
            computer.computer_id != token.computer_id
            or computer.tenant_id != token.tenant_id
            or computer.user_id != token.user_id
            or token.purpose is not purpose
        ):
            raise ComputerLeaseLostError("computer lease identity or purpose does not match")

    async def start(
        self,
        computer: AssistantComputer,
        token: ComputerLease,
        created: Callable[[str], Awaitable[None]],
    ) -> str:
        self._validate(computer, token, LeasePurpose.START)
        reference = await self.host.lifecycle(token, "ensure")
        if reference is None:
            raise RuntimeError("computer creation returned no instance")
        await created(reference)
        await self._ack(token, "init", active=False)
        return reference

    async def _ack(self, token: ComputerLease, action: ControlAction, *, active: bool) -> None:
        result = await self.host.call(token, action, {})
        gate_ack.validate(result, epoch=token.epoch, lease_id=token.lease_id, active=active)

    async def connect(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self._validate(computer, token, LeasePurpose.EXECUTE)
        await self._ack(token, "activate", active=True)

    async def renew(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self._validate(computer, token, LeasePurpose.EXECUTE)
        await self._ack(token, "renew", active=True)

    async def finish(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self._validate(computer, token, LeasePurpose.EXECUTE)
        await self._ack(token, "deactivate", active=False)

    async def recover(self, computer: AssistantComputer, token: ComputerLease) -> str | None:
        self._validate(computer, token, LeasePurpose.RECOVER)
        await self.host.lifecycle(token, "reset")
        return None

    async def validate_image(self) -> None:
        await self.host.check_image(self.image, provider=self.provider)

    async def upgrade(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self._validate(computer, token, LeasePurpose.RECOVER)
        reference = await self.host.lifecycle(token, "upgrade", image=self.image)
        if reference is not None:
            raise RuntimeError("computer migration did not confirm the old instance absent")

    async def execute(
        self,
        allocation: ComputerAllocation,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
        stdin: bytes | None = None,
    ) -> SandboxCommandResult:
        self._validate(allocation.computer, allocation.token, LeasePurpose.EXECUTE)
        payload: dict[str, object] = {
            "argv": list(argv),
            "cwd": WORKSPACE,
            "environment": dict(environment or {}),
            "timeout_seconds": timeout_seconds,
        }
        if stdin is not None:
            payload["stdin_base64"] = base64.b64encode(stdin).decode()
        return await self.host.call(allocation.token, "exec", payload)
