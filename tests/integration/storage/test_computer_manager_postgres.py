"""Manager orchestration against real ownership transactions and driver failures."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from harness.computer.manager import ComputerManager
from harness.computer.models import (
    AssistantComputer,
    ComputerBusyError,
    ComputerLease,
    ComputerRecoveryRequiredError,
    ComputerStartRejectedError,
    ComputerState,
)
from harness.computer.registry import ComputerRegistry
from harness.computer.storage import AssistantComputerRow
from harness.storage.database import SessionFactory
from tests.integration.storage.conftest import DatabaseFixture
from tests.integration.storage.test_computer_registry_postgres import expire


class Driver:
    provider = "opensandbox"
    image = "computer:v0"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_start: Exception | None = None
        self.fail_connect = self.fail_finish = self.fail_renew = False
        self.start_acknowledged = True
        self.recover_to: str | None = None

    async def start(
        self,
        computer: AssistantComputer,
        token: ComputerLease,
        created: Callable[[str], Awaitable[None]],
    ) -> str:
        self.calls.append("start")
        if self.start_acknowledged:
            await created("physical-a")
        if self.fail_start:
            raise self.fail_start
        return "physical-a"

    async def connect(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self.calls.append("connect")
        if self.fail_connect:
            raise RuntimeError("unreachable")

    async def renew(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self.calls.append("renew")
        if self.fail_renew:
            raise RuntimeError("heartbeat unavailable")

    async def finish(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self.calls.append("finish")
        if self.fail_finish:
            raise RuntimeError("cleanup unconfirmed")

    async def recover(
        self,
        computer: AssistantComputer,
        token: ComputerLease,
    ) -> str | None:
        self.calls.append("recover")
        return self.recover_to


def manager(sessions: SessionFactory, driver: Driver) -> ComputerManager:
    return ComputerManager(
        ComputerRegistry(sessions), driver, owner="worker-a", heartbeat_seconds=0.02
    )


@pytest.mark.asyncio
async def test_registered_computer_with_provider_instance_is_recovered(database: DatabaseFixture):
    _, sessions = database
    driver = Driver()
    driver.recover_to = "physical-existing"
    m = manager(sessions, driver)
    await m.registry.ensure("t", "u", provider=driver.provider, image=driver.image)
    async with sessions.begin() as session:
        computer = await session.get(AssistantComputerRow, ("t", "u"))
        assert computer is not None
        computer.provider_ref = driver.recover_to

    ready = await m.ready("t", "u")

    assert ready.state is ComputerState.IDLE
    assert ready.provider_ref == "physical-existing"
    assert driver.calls == ["recover"]


@pytest.mark.asyncio
async def test_two_executions_reuse_identity_and_confirm_cleanup_before_release(
    database: DatabaseFixture,
):
    _, sessions = database
    driver = Driver()
    m = manager(sessions, driver)
    async with m.execution("t", "u", run_id="r1", session_id="s") as first:
        assert first.computer.state is ComputerState.RUNNING
        assert first.computer.epoch == first.token.epoch
        async with asyncio.timeout(2):
            while "renew" not in driver.calls:
                await asyncio.sleep(0.01)
        with pytest.raises(ComputerBusyError):
            async with manager(sessions, driver).execution("t", "u", run_id="r2", session_id="s"):
                raise AssertionError("second holder entered")
    assert (await m.registry.get("t", "u")).state is ComputerState.IDLE
    async with manager(sessions, driver).execution("t", "u", run_id="r2", session_id="s") as second:
        assert second.computer.computer_id == first.computer.computer_id
        assert second.computer.volume_ref == first.computer.volume_ref
        assert second.token.epoch > first.token.epoch
    assert driver.calls.count("start") == 1 and driver.calls.count("finish") == 2


@pytest.mark.asyncio
async def test_user_work_exception_still_drains_the_execution(database: DatabaseFixture):
    _, sessions = database
    driver = Driver()
    m = manager(sessions, driver)
    with pytest.raises(ValueError, match="work failed"):
        async with m.execution("t", "u", run_id="r", session_id="s"):
            raise ValueError("work failed")
    assert driver.calls[-1] == "finish"
    assert (await m.registry.get("t", "u")).state is ComputerState.IDLE


@pytest.mark.asyncio
async def test_unconfirmed_cleanup_blocks_reuse_until_recovery(database: DatabaseFixture):
    _, sessions = database
    driver = Driver()
    m = manager(sessions, driver)
    driver.fail_finish = True
    allocation = None
    with pytest.raises(RuntimeError, match="unconfirmed"):
        async with m.execution("t", "u", run_id="r1", session_id="s") as allocation:
            pass
    assert allocation is not None
    with pytest.raises(ComputerBusyError):
        await manager(sessions, driver).ready("t", "u")
    await expire(sessions, allocation.token)
    driver.fail_finish = False
    async with manager(sessions, driver).execution(
        "t", "u", run_id="r2", session_id="s"
    ) as next_run:
        assert next_run.computer.computer_id == allocation.computer.computer_id
    assert "recover" in driver.calls
    assert driver.calls.count("start") == 2


@pytest.mark.asyncio
async def test_heartbeat_failure_cancels_work_then_fences_its_processes(database: DatabaseFixture):
    _, sessions = database
    driver = Driver()
    m = manager(sessions, driver)
    driver.fail_renew = True
    with pytest.raises(RuntimeError, match="heartbeat unavailable"):
        async with m.execution("t", "u", run_id="r", session_id="s"):
            await asyncio.sleep(3)
            raise AssertionError("work survived failed heartbeat")
    assert driver.calls[-1] == "finish"
    assert (await m.registry.get("t", "u")).state is ComputerState.IDLE


@pytest.mark.asyncio
async def test_definite_creation_rejection_can_be_retried_without_blind_recovery(
    database: DatabaseFixture,
):
    _, sessions = database
    driver = Driver()
    driver.start_acknowledged = False
    driver.fail_start = ComputerStartRejectedError("invalid image")
    m = manager(sessions, driver)
    with pytest.raises(ComputerStartRejectedError):
        await m.ready("t", "u")
    failed = await m.registry.get("t", "u")
    assert failed.state is ComputerState.FAILED and not failed.volume_ready
    driver.fail_start = None
    result = await m.ready("t", "u")
    assert result.computer_id == failed.computer_id and result.volume_ready
    assert "recover" not in driver.calls


@pytest.mark.asyncio
async def test_unknown_creation_outcome_is_not_marked_rejected_or_retried(
    database: DatabaseFixture,
):
    _, sessions = database
    driver = Driver()
    driver.start_acknowledged = False
    driver.fail_start = TimeoutError("creation uncertain")
    m = manager(sessions, driver)
    with pytest.raises(TimeoutError):
        await m.ready("t", "u")
    state = await m.registry.get("t", "u")
    assert state.state is ComputerState.STARTING and state.provider_ref is None
    with pytest.raises(ComputerBusyError):
        await m.ready("t", "u")
    assert driver.calls == ["start"]


@pytest.mark.asyncio
async def test_creation_ack_is_durable_before_initialization_finishes(database: DatabaseFixture):
    _, sessions = database
    driver = Driver()
    driver.fail_start = RuntimeError("initialization failed")
    m = manager(sessions, driver)
    with pytest.raises(RuntimeError, match="initialization failed"):
        await m.ready("t", "u")
    state = await m.registry.get("t", "u")
    assert state.state is ComputerState.STARTING and state.provider_ref == "physical-a"
    assert not state.volume_ready


@pytest.mark.asyncio
async def test_volume_is_not_forgetten_after_confirmed_instance_loss(database: DatabaseFixture):
    _, sessions = database
    m = manager(sessions, Driver())
    initial = await m.ready("t", "u")
    recovery = await m.registry.claim_recovery("t", "u", owner="operator")
    restored = await m.registry.complete_recovery(recovery, provider_ref=None)
    assert restored.volume_ready and restored.volume_ref == initial.volume_ref
    with pytest.raises(ComputerRecoveryRequiredError):
        await m.registry.claim_execution("t", "u", owner="worker", run_id="r", session_id="s")
