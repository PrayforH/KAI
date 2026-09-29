"""Execution authority inherited by SDK callbacks, revoked when the gate exits."""

import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass


class ExecutionOwnershipLostError(RuntimeError):
    """The current execution no longer owns its Session."""


@dataclass
class ExecutionOwnership:
    verify: Callable[[], Awaitable[None]]
    active: bool = True
    expires_at: float = float("inf")

    def check_current(self) -> None:
        # A paused Worker must not publish buffered output after its lease
        # expires, even before the heartbeat task gets another scheduling turn.
        if not self.active or time.monotonic() >= self.expires_at:
            raise ExecutionOwnershipLostError("Session execution authority was revoked")

    async def check(self) -> None:
        self.check_current()
        await self.verify()
        self.check_current()


execution_ownership: ContextVar[ExecutionOwnership | None] = ContextVar(
    "harness_execution_ownership", default=None
)
