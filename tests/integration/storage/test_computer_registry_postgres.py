"""Separate database connections compete for the same physical computer owner.

These tests use PostgreSQL locking and database time, not an in-process mutex.
No production database or user work volume is touched.
"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text

from harness.computer.models import (
    AssistantComputer,
    ComputerBusyError,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerRecoveryRequiredError,
    ComputerState,
    LeaseState,
)
from harness.computer.registry import ComputerRegistry
from harness.computer.storage import ComputerLeaseRow
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from tests.integration.storage.conftest import DatabaseFixture


async def ready(sessions: SessionFactory) -> tuple[ComputerRegistry, AssistantComputer]:
    registry = ComputerRegistry(sessions)
    await registry.ensure(
        "tenant-a", "user-a", provider="opensandbox", image="desktop@sha256:fixed"
    )
    start = await registry.claim_start("tenant-a", "user-a", owner="manager-a")
    computer = await registry.complete_start(start, provider_ref="sandbox-a")
    return registry, computer


async def execute(registry: ComputerRegistry, *, owner: str = "worker-a") -> ComputerLease:
    return await registry.claim_execution(
        "tenant-a", "user-a", owner=owner, run_id="run-a", session_id="session-a"
    )


async def expire(sessions: SessionFactory, token: ComputerLease) -> None:
    # Move both endpoints, preserving the real schema's positive-lifetime constraint.
    async with sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE computer_execution_leases SET "
                "created_at=clock_timestamp()-interval '2 hours', "
                "expires_at=clock_timestamp()-interval '1 hour' WHERE lease_id=:id"
            ),
            {"id": token.lease_id},
        )


@pytest.mark.asyncio
async def test_concurrent_registration_reserves_one_identity_and_disk(database: DatabaseFixture):
    _, sessions = database
    computers = await asyncio.gather(
        *(
            ComputerRegistry(sessions).ensure("t", "u", provider="opensandbox", image="desktop:v1")
            for _ in range(8)
        )
    )
    assert len({c.computer_id for c in computers}) == 1
    assert len({c.volume_ref for c in computers}) == 1
    assert all(c.state is ComputerState.REGISTERED and c.epoch == 0 for c in computers)


@pytest.mark.asyncio
async def test_two_turns_and_registry_restart_reuse_computer_and_disk(database: DatabaseFixture):
    _, sessions = database
    registry, initial = await ready(sessions)
    first = await execute(registry)
    assert first.process_group == first.lease_id
    await registry.finish_execution(first)

    restarted = ComputerRegistry(sessions)
    second = await execute(restarted, owner="worker-b")
    assert second.epoch > first.epoch
    assert second.process_group != first.process_group
    result = await restarted.finish_execution(second)
    assert result.state is ComputerState.IDLE
    assert result.last_active_at is not None
    assert (result.computer_id, result.provider_ref, result.volume_ref) == (
        initial.computer_id,
        initial.provider_ref,
        initial.volume_ref,
    )


@pytest.mark.asyncio
async def test_workers_cannot_acquire_same_computer_concurrently(database: DatabaseFixture):
    _, sessions = database
    await ready(sessions)
    results = await asyncio.gather(
        *(execute(ComputerRegistry(sessions), owner=f"worker-{i}") for i in range(6)),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ComputerLease) for r in results) == 1
    assert sum(isinstance(r, ComputerBusyError) for r in results) == 5


@pytest.mark.asyncio
async def test_expired_lease_requires_provider_recovery_not_automatic_reuse(
    database: DatabaseFixture,
):
    _, sessions = database
    registry, initial = await ready(sessions)
    old = await execute(registry)
    await expire(sessions, old)
    with pytest.raises(ComputerLeaseLostError):
        await registry.renew(old)
    with pytest.raises(ComputerLeaseLostError):
        await registry.finish_execution(old)
    with pytest.raises(ComputerRecoveryRequiredError):
        await execute(ComputerRegistry(sessions), owner="worker-b")

    recovery = await registry.claim_recovery("tenant-a", "user-a", owner="manager-b")
    assert recovery.epoch == old.epoch + 1
    assert (await registry.get("tenant-a", "user-a")).state is ComputerState.RECOVERING
    with pytest.raises(ComputerBusyError):
        await execute(registry)
    with pytest.raises(ComputerLeaseLostError):
        await registry.finish_execution(recovery)
    await registry.complete_recovery(recovery, provider_ref="sandbox-a")
    current = await execute(registry, owner="worker-b")
    with pytest.raises(ComputerLeaseLostError):
        await registry.finish_execution(old)
    assert (await registry.get("tenant-a", "user-a")).state is ComputerState.RUNNING
    await registry.finish_execution(current)
    assert (await registry.get("tenant-a", "user-a")).volume_ref == initial.volume_ref

    async with sessions() as session:
        history = (
            await session.scalars(select(ComputerLeaseRow).order_by(ComputerLeaseRow.epoch))
        ).all()
        assert [lease.state for lease in history] == ["released", "expired", "released", "released"]


@pytest.mark.asyncio
async def test_lost_instance_recovery_preserves_disk_and_requires_explicit_start(
    database: DatabaseFixture,
):
    _, sessions = database
    registry, initial = await ready(sessions)
    recovery = await registry.claim_recovery("tenant-a", "user-a", owner="manager-a")
    recovered = await registry.complete_recovery(recovery, provider_ref=None)
    assert recovered.state is ComputerState.REGISTERED
    assert recovered.volume_ref == initial.volume_ref
    with pytest.raises(ComputerRecoveryRequiredError):
        await execute(registry)
    start = await registry.claim_start("tenant-a", "user-a", owner="manager-b")
    restarted = await registry.complete_start(start, provider_ref="sandbox-b")
    assert restarted.computer_id == initial.computer_id
    assert restarted.volume_ref == initial.volume_ref
    assert restarted.provider_ref != initial.provider_ref


@pytest.mark.asyncio
async def test_crashed_start_cannot_publish_after_recovery_has_begun(database: DatabaseFixture):
    _, sessions = database
    registry = ComputerRegistry(sessions)
    initial = await registry.ensure("t", "u", provider="opensandbox", image="desktop:v1")
    old = await registry.claim_start("t", "u", owner="manager-a")
    await expire(sessions, old)
    with pytest.raises(ComputerRecoveryRequiredError):
        await registry.claim_start("t", "u", owner="manager-b")
    recovery = await registry.claim_recovery("t", "u", owner="manager-b")
    with pytest.raises(ComputerLeaseLostError):
        await registry.complete_start(old, provider_ref="old-sandbox")
    # Provider discovery can recover the original instance by computer metadata.
    result = await registry.complete_recovery(recovery, provider_ref="old-sandbox")
    assert result.computer_id == initial.computer_id
    assert result.state is ComputerState.IDLE


@pytest.mark.asyncio
async def test_expired_recovery_requires_another_fenced_recovery(database: DatabaseFixture):
    _, sessions = database
    registry, _ = await ready(sessions)
    first = await registry.claim_recovery("tenant-a", "user-a", owner="manager-a")
    await expire(sessions, first)
    second = await registry.claim_recovery("tenant-a", "user-a", owner="manager-b")
    with pytest.raises(ComputerLeaseLostError):
        await registry.complete_recovery(first, provider_ref=None)
    await registry.complete_recovery(second, provider_ref="sandbox-a")
    assert second.epoch > first.epoch


@pytest.mark.asyncio
async def test_tenant_user_and_lease_identity_are_not_interchangeable(database: DatabaseFixture):
    _, sessions = database
    registry, first = await ready(sessions)
    other_user = await registry.ensure(
        "tenant-a", "user-b", provider="opensandbox", image="desktop:v1"
    )
    other_tenant = await registry.ensure(
        "tenant-b", "user-a", provider="opensandbox", image="desktop:v1"
    )
    assert len({c.volume_ref for c in [first, other_user, other_tenant]}) == 3
    with pytest.raises(NotFoundError):
        await registry.get("tenant-c", "user-a")
    token = await execute(registry)
    for change in [
        {"tenant_id": "tenant-b"},
        {"user_id": "user-b"},
        {"owner": "worker-b"},
        {"computer_id": other_user.computer_id},
        {"epoch": token.epoch + 1},
        {"run_id": "another-run"},
        {"session_id": "another-session"},
    ]:
        with pytest.raises(ComputerLeaseLostError):
            await registry.renew(token.model_copy(update=change))
    assert (await registry.renew(token)).state is LeaseState.ACTIVE


@pytest.mark.asyncio
async def test_existing_computer_configuration_is_never_silently_replaced(
    database: DatabaseFixture,
):
    _, sessions = database
    registry, initial = await ready(sessions)
    for provider, image in [("other", initial.image), (initial.provider, "desktop:new")]:
        with pytest.raises(ConflictError):
            await registry.ensure("tenant-a", "user-a", provider=provider, image=image)
    current = await registry.get("tenant-a", "user-a")
    assert current == initial
    with pytest.raises(ConflictError):
        await registry.claim_start("tenant-a", "user-a", owner="manager-b")


@pytest.mark.asyncio
async def test_renewal_after_waiting_for_lock_uses_current_database_time(database: DatabaseFixture):
    _, sessions = database
    registry, _ = await ready(sessions)
    token = await execute(registry)
    async with sessions.begin() as blocker:
        await blocker.execute(
            text("SELECT 1 FROM assistant_computers WHERE tenant_id='tenant-a' FOR UPDATE")
        )
        task = asyncio.create_task(ComputerRegistry(sessions).renew(token))
        try:
            # Poll the real PostgreSQL waiter, not a timing guess about task startup.
            for _ in range(100):
                waiting = await blocker.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE NOT granted "
                        "AND pg_backend_pid() = ANY(pg_blocking_pids(pid))"
                    )
                )
                if waiting:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("renewal did not reach the database lock")
            await blocker.execute(
                text(
                    "UPDATE computer_execution_leases SET "
                    "created_at=clock_timestamp()-interval '2 hours', "
                    "expires_at=clock_timestamp()-interval '1 hour' WHERE lease_id=:id"
                ),
                {"id": token.lease_id},
            )
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
    with pytest.raises(ComputerLeaseLostError):
        await task


@pytest.mark.asyncio
async def test_renewal_extends_database_lease_without_mutating_old_token(database: DatabaseFixture):
    _, sessions = database
    registry, _ = await ready(sessions)
    token = await execute(registry)
    renewed = await ComputerRegistry(sessions, lease_ttl_seconds=300).renew(token)
    assert renewed.expires_at > token.expires_at + timedelta(seconds=170)
    # Renewed copies do not invalidate the existing execution's epoch token.
    await registry.finish_execution(token)
    with pytest.raises(ComputerLeaseLostError):
        await registry.renew(renewed)


@pytest.mark.asyncio
async def test_work_for_different_owners_does_not_share_a_global_lock(database: DatabaseFixture):
    _, sessions = database
    registry, _ = await ready(sessions)
    await registry.ensure("tenant-a", "user-b", provider="opensandbox", image="desktop:v1")
    async with sessions.begin() as blocker:
        await blocker.execute(
            text(
                "SELECT 1 FROM assistant_computers WHERE tenant_id='tenant-a' AND user_id='user-a' "
                "FOR UPDATE"
            )
        )
        async with asyncio.timeout(3):
            token = await registry.claim_start("tenant-a", "user-b", owner="manager-b")
    assert token.user_id == "user-b"
