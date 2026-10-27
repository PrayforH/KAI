"""Persistent computers through the authenticated host control service.

There is no privileged execd endpoint inside these computers. Lifecycle uses
one deterministic Docker name per registered computer, so a delayed create
cannot mount the work disk in a second instance. Control always resolves the
current registry holder independently of this client's cached allocation.
"""

import base64
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast

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
    async def lifecycle(self, token: ComputerLease, operation: str) -> str | None: ...


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
        try:
            value = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError("computer control acknowledgement is invalid") from None
        saved = cast(dict[str, object], value) if isinstance(value, dict) else {}
        if (
            result.exit_code != 0
            or saved.get("epoch") != token.epoch
            or saved.get("lease_id") != token.lease_id
            or saved.get("active") is not active
        ):
            raise RuntimeError("computer control acknowledgement does not match its lease")

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
