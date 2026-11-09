"""Desktop control state machine against PostgreSQL.

One locked row per computer; concurrency uses real database locking on the
isolated integration database. No production computer or lease is touched.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from harness.api.app import create_memory_app
from harness.computer.desktop import (
    AGENT_TTL_SECONDS,
    DRAIN_WINDOW_SECONDS,
    DesktopControlService,
    DesktopOccupiedError,
    DesktopState,
)
from harness.computer.desktop_registry import DesktopRegistry, bot_desktop_key
from harness.computer.models import ComputerBusyError
from harness.computer.registry import ComputerRegistry
from tests.integration.storage.conftest import DatabaseFixture


async def seeded(database: DatabaseFixture) -> DesktopControlService:
    _, sessions = database
    await ComputerRegistry(sessions).ensure(
        "tenant-a", "user-a", provider="vm-computer", image="sha256:" + "a" * 64
    )
    return DesktopControlService(sessions)


@pytest.mark.asyncio
async def test_observe_to_agent_and_release_returns_to_observe(database: DatabaseFixture):
    service = await seeded(database)
    first = await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    assert first.state is DesktopState.AGENT and first.owner == "worker-1"
    assert first.epoch == 1 and first.expires_at is not None
    released = await service.release_agent("tenant-a", "user-a", owner="worker-1")
    assert released.state is DesktopState.OBSERVE and released.epoch == 2
    assert released.owner is None and released.expires_at is None


@pytest.mark.asyncio
async def test_second_agent_cannot_acquire_while_one_owns(database: DatabaseFixture):
    service = await seeded(database)
    await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    with pytest.raises(DesktopOccupiedError):
        await service.acquire_agent("tenant-a", "user-a", owner="worker-2")
    # The same owner renews instead of failing.
    renewed = await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    assert renewed.owner == "worker-1" and renewed.revision > 1


@pytest.mark.asyncio
async def test_takeover_fences_agent_and_needs_the_node_confirmation(database: DatabaseFixture):
    service = await seeded(database)
    agent = await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    pending = await service.request_human("tenant-a", "user-a")
    assert pending.state is DesktopState.TAKEOVER_PENDING
    # The epoch bump fences the agent's bound epoch immediately.
    assert pending.epoch == agent.epoch + 1 and pending.owner is None
    # A pending window that expires without the node's drain acknowledgement
    # is a failed takeover: it falls back to observation, never to human.
    async with service._sessions.begin() as session:
        from harness.computer.storage import ComputerDesktopLeaseRow as Row

        row = await session.scalar(select(Row).where(Row.tenant_id == "tenant-a"))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    failed = await service.view("tenant-a", "user-a")
    assert failed.state is DesktopState.OBSERVE
    assert failed.epoch == pending.epoch + 1

    # A confirmed drain publishes human ownership exactly once, guarded by
    # the epoch the node observed while pending.
    pending = await service.request_human("tenant-a", "user-a")
    stale = await service.confirm_human("tenant-a", "user-a", expected_epoch=pending.epoch + 5)
    assert stale.state is DesktopState.TAKEOVER_PENDING
    human = await service.confirm_human("tenant-a", "user-a", expected_epoch=pending.epoch)
    assert human.state is DesktopState.HUMAN
    assert human.epoch == pending.epoch + 1
    # The confirmation starts the human window; the very next read must not
    # fall back to observation, or the takeover would last a single poll.
    again = await service.view("tenant-a", "user-a")
    assert again.state is DesktopState.HUMAN
    assert again.expires_at is not None
    assert again.expires_at > datetime.now(UTC) + timedelta(seconds=60)
    # While the human owns the desktop, the fenced agent owner is refused.
    with pytest.raises(DesktopOccupiedError):
        await service.acquire_agent("tenant-a", "user-a", owner="worker-1")


@pytest.mark.asyncio
async def test_return_from_human_resets_input_ownership(database: DatabaseFixture):
    service = await seeded(database)
    await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    await service.request_human("tenant-a", "user-a")
    returned = await service.release_human("tenant-a", "user-a")
    assert returned.state is DesktopState.OBSERVE
    # After return the agent may acquire input again (fresh epoch).
    again = await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    assert again.state is DesktopState.AGENT and again.epoch > returned.epoch


@pytest.mark.asyncio
async def test_human_takeover_overrides_agent_and_blocks_both_sides(database: DatabaseFixture):
    service = await seeded(database)
    await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    pending = await service.request_human("tenant-a", "user-a")
    with pytest.raises(DesktopOccupiedError):
        await service.acquire_agent("tenant-a", "user-a", owner="worker-2")
    assert pending.input_allowed(kind="agent", epoch=pending.epoch) is False
    assert pending.input_allowed(kind="human") is False


@pytest.mark.asyncio
async def test_agent_ttl_expiry_falls_back_to_observe(database: DatabaseFixture):
    service = await seeded(database)
    agent = await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    assert agent.expires_at is not None
    assert agent.expires_at - datetime.now(UTC) <= timedelta(seconds=AGENT_TTL_SECONDS)
    async with service._sessions.begin() as session:
        from harness.computer.storage import ComputerDesktopLeaseRow as Row

        row = await session.scalar(select(Row).where(Row.tenant_id == "tenant-a"))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    settled = await service.view("tenant-a", "user-a")
    assert settled.state is DesktopState.OBSERVE and settled.epoch > agent.epoch


@pytest.mark.asyncio
async def test_drain_window_is_bounded_and_deliberate(database: DatabaseFixture):
    assert 0 < DRAIN_WINDOW_SECONDS <= 5


@pytest.mark.asyncio
async def test_desktop_row_requires_a_registered_computer(database: DatabaseFixture):
    _, sessions = database
    service = DesktopControlService(sessions)
    from harness.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.view("tenant-a", "nobody")


@pytest.mark.asyncio
async def test_heartbeat_keeps_an_active_takeover_alive(database: DatabaseFixture):
    service = await seeded(database)
    pending = await service.request_human("tenant-a", "user-a")
    human = await service.confirm_human("tenant-a", "user-a", expected_epoch=pending.epoch)
    assert human.state is DesktopState.HUMAN
    # The viewer refreshes on every poll; the window must stay open and the
    # agent must stay fenced.
    for _ in range(3):
        refreshed = await service.refresh_human("tenant-a", "user-a")
        assert refreshed.state is DesktopState.HUMAN
        assert refreshed.expires_at is not None
        assert refreshed.expires_at > datetime.now(UTC) + timedelta(seconds=60)
    with pytest.raises(DesktopOccupiedError):
        await service.acquire_agent("tenant-a", "user-a", owner="worker-1")

@pytest.mark.asyncio
async def test_bot_preview_resolves_only_the_callers_slot(database: DatabaseFixture):
    _, sessions = database
    registry = DesktopRegistry(sessions)
    app = create_memory_app()
    app.state.container = replace(app.state.container, computer_desktops=registry)
    computer = await ComputerRegistry(sessions).ensure(
        "tenant-a", "user-1", provider="vm-computer", image="sha256:" + "a" * 64
    )
    slot = await registry.ensure(
        "tenant-a", "user-1", bot_desktop_key("specialist_fixture"),
        computer_id=computer.computer_id,
    )
    url = "/v1/studio/assistant/computer/desktops/preview?bot_id=specialist_fixture"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owned = await client.get(url, headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"})
        other = await client.get(url, headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"})
    assert owned.json() == {"desktopId": slot.desktop_id, "state": "created"}
    assert other.status_code == 404

@pytest.mark.asyncio
async def test_idle_reap_and_first_execution_share_the_slot_lock(database: DatabaseFixture):
    _, sessions = database
    owner = ("tenant-a", "user-a")
    computers, desktops = ComputerRegistry(sessions), DesktopRegistry(sessions)
    computer = await computers.ensure(*owner, provider="vm-computer", image="sha256:" + "a" * 64)
    start = await computers.claim_start(*owner, owner="worker")
    await computers.complete_start(start, provider_ref="fixture-guest")
    slot = await desktops.ensure(*owner, "bot-a", computer_id=computer.computer_id)

    async def claim():
        return await computers.claim_execution(
            *owner, owner="worker", run_id="probe-run", session_id="probe-session",
            desktop_id=slot.desktop_id,
        )

    snapshot = await desktops.mark_started(*owner, slot.desktop_id)
    lease = await claim()
    stop = AsyncMock()
    assert not await desktops.reap(snapshot, stop)
    stop.assert_not_awaited()
    await computers.finish_execution(lease)

    snapshot = await desktops.get_by_id(*owner, slot.desktop_id)
    began_stopping, finish_stopping = asyncio.Event(), asyncio.Event()

    async def stop_guest(_desktop):
        began_stopping.set()
        await finish_stopping.wait()

    stopping = asyncio.create_task(desktops.reap(snapshot, stop_guest))
    await asyncio.wait_for(began_stopping.wait(), timeout=2)
    waiting = asyncio.create_task(claim())
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiting), timeout=0.1)
    finally:
        finish_stopping.set()
    assert await stopping
    with pytest.raises(ComputerBusyError, match="reserve it again"):
        await waiting

    revived = await desktops.ensure(*owner, "bot-a", computer_id=computer.computer_id)
    assert revived.desktop_id == slot.desktop_id
    assert revived.display == slot.display
    await computers.finish_execution(await claim())
