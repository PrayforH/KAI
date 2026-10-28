"""Desktop control state machine against PostgreSQL.

One locked row per computer; concurrency uses real database locking on the
isolated integration database. No production computer or lease is touched.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from harness.computer.desktop import (
    AGENT_TTL_SECONDS,
    PENDING_GRACE_SECONDS,
    DesktopControlService,
    DesktopOccupiedError,
    DesktopState,
)
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
async def test_takeover_fences_agent_and_settles_to_human(database: DatabaseFixture):
    service = await seeded(database)
    agent = await service.acquire_agent("tenant-a", "user-a", owner="worker-1")
    pending = await service.request_human("tenant-a", "user-a")
    assert pending.state is DesktopState.TAKEOVER_PENDING
    # The epoch bump fences the agent's bound epoch immediately.
    assert pending.epoch == agent.epoch + 1 and pending.owner is None
    # Grace expiry settles to human on the next observation.
    async with service._sessions.begin() as session:
        from harness.computer.storage import ComputerDesktopLeaseRow as Row

        row = await session.scalar(select(Row).where(Row.tenant_id == "tenant-a"))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    human = await service.view("tenant-a", "user-a")
    assert human.state is DesktopState.HUMAN
    assert human.epoch == pending.epoch
    # Settling starts the human window; the very next read must not fall back
    # to observation, or the takeover would last a single poll.
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
async def test_pending_grace_is_short_and_deliberate(database: DatabaseFixture):
    assert 0 < PENDING_GRACE_SECONDS <= 5


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
    await service.request_human("tenant-a", "user-a")
    async with service._sessions.begin() as session:
        from harness.computer.storage import ComputerDesktopLeaseRow as Row

        row = await session.scalar(select(Row).where(Row.tenant_id == "tenant-a"))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    human = await service.view("tenant-a", "user-a")
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
