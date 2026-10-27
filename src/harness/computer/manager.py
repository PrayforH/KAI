"""Coordinate durable ownership with the physical Computer driver.

Provider calls never hold database row locks. A lease heartbeat is a sibling
task: failure cancels work instead of allowing an unfenced execution to carry
on. Database ownership is released only after physical cleanup is confirmed.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerStartRejectedError,
    ComputerState,
)
from harness.computer.registry import ComputerRegistry


class ComputerDriver(Protocol):
    provider: str
    image: str

    async def start(
        self,
        computer: AssistantComputer,
        token: ComputerLease,
        created: Callable[[str], Awaitable[None]],
    ) -> str: ...

    async def connect(self, computer: AssistantComputer, token: ComputerLease) -> None: ...

    async def renew(self, computer: AssistantComputer, token: ComputerLease) -> None: ...

    async def finish(self, computer: AssistantComputer, token: ComputerLease) -> None: ...

    async def recover(
        self,
        computer: AssistantComputer,
        token: ComputerLease,
    ) -> str | None: ...


@dataclass(frozen=True)
class ComputerAllocation:
    computer: AssistantComputer
    token: ComputerLease


class ComputerManager:
    def __init__(
        self,
        registry: ComputerRegistry,
        driver: ComputerDriver,
        *,
        owner: str,
        heartbeat_seconds: float | None = None,
    ) -> None:
        interval = (
            heartbeat_seconds if heartbeat_seconds is not None else registry.lease_ttl_seconds / 3
        )
        if not owner.strip() or not 0 < interval < registry.lease_ttl_seconds:
            raise ValueError("computer manager requires an owner and a heartbeat within its lease")
        self.registry, self.driver, self.owner = registry, driver, owner
        self._interval = interval

    async def ready(self, tenant: str, user: str) -> AssistantComputer:
        computer = await self.registry.ensure(
            tenant, user, provider=self.driver.provider, image=self.driver.image
        )
        for _ in range(3):
            if computer.state is ComputerState.IDLE:
                return computer
            if computer.state in {ComputerState.REGISTERED, ComputerState.FAILED}:
                token = await self.registry.claim_start(tenant, user, owner=self.owner)

                try:
                    async with self._heartbeat(computer, token, physical=False):
                        reference = await self.driver.start(
                            computer, token, partial(self.registry.record_start_instance, token)
                        )
                except ComputerStartRejectedError:
                    await self.registry.reject_start(token)
                    raise
                computer = await self.registry.complete_start(token, provider_ref=reference)
            else:
                # A live holder raises Busy. Expiry only permits recovery, not
                # another execution on a potentially still-writing instance.
                token = await self.registry.claim_recovery(tenant, user, owner=self.owner)
                async with self._heartbeat(computer, token, physical=False):
                    reference = await self.driver.recover(computer, token)
                computer = await self.registry.complete_recovery(token, provider_ref=reference)
        raise RuntimeError("computer recovery did not reach a ready state")

    @asynccontextmanager
    async def execution(
        self, tenant: str, user: str, *, run_id: str, session_id: str
    ) -> AsyncGenerator[ComputerAllocation]:
        computer = await self.ready(tenant, user)
        token = await self.registry.claim_execution(
            tenant, user, owner=self.owner, run_id=run_id, session_id=session_id
        )
        computer = await self.registry.get(tenant, user)
        # Activation is outside the user-work try/finally: a failed/uncertain
        # connection retains ownership for recovery, rather than assuming that
        # no remote process could have started.
        async with self._heartbeat(computer, token, physical=False):
            await self.driver.connect(computer, token)
        try:
            async with self._heartbeat(computer, token, physical=True):
                yield ComputerAllocation(computer, token)
        finally:
            # These calls are deliberately ordered. If draining or its reply is
            # uncertain, keep the database lease; never unlock the next Run.
            await self.driver.finish(computer, token)
            await self.registry.finish_execution(token)

    @asynccontextmanager
    async def _heartbeat(
        self, computer: AssistantComputer, token: ComputerLease, *, physical: bool
    ) -> AsyncGenerator[None]:
        stop = asyncio.Event()

        async def beat() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._interval)
                    return
                except TimeoutError:
                    current = await self.registry.renew(token)
                    if physical:
                        await self.driver.renew(computer, current)

        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(beat())
                try:
                    yield
                finally:
                    stop.set()
        except ExceptionGroup as error:
            if len(error.exceptions) == 1:
                raise error.exceptions[0] from None
            raise
