"""Durable sandbox leases: ownership, fencing epochs, expiry and release."""

from datetime import UTC, datetime, timedelta

import pytest

from harness.sandbox.lease import (
    SandboxLeaseError,
    SandboxLeaseService,
    SandboxLeaseState,
)
from harness.storage.sandbox_lease_repository import InMemorySandboxLeaseRepository

START = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)


def service(*, ttl: int = 3600) -> tuple[SandboxLeaseService, list[datetime]]:
    clock = [START]
    counter = {"n": 0}

    def ids(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return (
        SandboxLeaseService(
            InMemorySandboxLeaseRepository(),
            clock=lambda: clock[0],
            id_generator=ids,
            default_ttl_seconds=ttl,
        ),
        clock,
    )


async def acquire(subject: SandboxLeaseService, **overrides: object):
    values: dict[str, object] = {
        "tenant_id": "tenant-a",
        "session_id": "session-1",
        "run_id": "run-1",
        "owner": "worker-1",
        "provider": "opensandbox-deferred",
        "sandbox_id": "sandbox-1",
    }
    values.update(overrides)
    return await subject.acquire(**values)  # pyright: ignore[reportArgumentType]


@pytest.mark.asyncio
async def test_acquire_records_owner_epoch_and_expiry() -> None:
    subject, _ = service(ttl=600)
    lease = await acquire(subject)

    assert lease.epoch == 1
    assert lease.state is SandboxLeaseState.ACTIVE
    assert lease.expires_at == START + timedelta(seconds=600)
    assert lease.renewed_at == lease.created_at == START
    assert [item.lease_id for item in await subject.live("tenant-a")] == [lease.lease_id]
    assert (await subject.for_run("tenant-a", "run-1")) == lease


@pytest.mark.asyncio
async def test_a_second_live_acquire_for_the_session_is_refused() -> None:
    subject, _ = service()
    await acquire(subject)
    with pytest.raises(SandboxLeaseError, match="live sandbox lease"):
        await acquire(subject, run_id="run-2", owner="worker-2")


@pytest.mark.asyncio
async def test_an_expired_lease_is_superseded_with_a_higher_epoch() -> None:
    subject, clock = service(ttl=60)
    first = await acquire(subject)
    clock[0] = START + timedelta(seconds=61)

    second = await acquire(subject, run_id="run-2", owner="worker-2", sandbox_id="sandbox-2")

    assert second.epoch == first.epoch + 1
    assert second.sandbox_id == "sandbox-2"
    live = await subject.live("tenant-a")
    assert [item.lease_id for item in live] == [first.lease_id, second.lease_id] or [
        item.lease_id for item in live
    ] == [second.lease_id, first.lease_id]
    expired = await subject.expired()
    assert [item.lease_id for item in expired] == [first.lease_id]


@pytest.mark.asyncio
async def test_renew_extends_expiry_and_rejects_a_stale_epoch() -> None:
    subject, clock = service(ttl=60)
    lease = await acquire(subject)
    clock[0] = START + timedelta(seconds=30)

    renewed = await subject.renew("tenant-a", lease.lease_id, epoch=lease.epoch, ttl_seconds=120)
    assert renewed.expires_at == clock[0] + timedelta(seconds=120)
    assert renewed.renewed_at == clock[0]
    assert await subject.expired() == []

    with pytest.raises(SandboxLeaseError, match="epoch no longer matches"):
        await subject.renew("tenant-a", lease.lease_id, epoch=lease.epoch + 1)


@pytest.mark.asyncio
async def test_release_ends_the_lease_and_a_stale_epoch_cannot_release() -> None:
    subject, _ = service()
    lease = await acquire(subject)

    with pytest.raises(SandboxLeaseError, match="epoch no longer matches"):
        await subject.release("tenant-a", lease.lease_id, epoch=lease.epoch + 1)

    await subject.release("tenant-a", lease.lease_id, epoch=lease.epoch)
    stored = await subject.for_run("tenant-a", "run-1")
    assert stored is not None and stored.state is SandboxLeaseState.RELEASED
    assert stored.released_at == START
    assert await subject.live("tenant-a") == []

    # Releasing twice stays a no-op rather than resurrecting the lease.
    await subject.release("tenant-a", lease.lease_id, epoch=lease.epoch)


@pytest.mark.asyncio
async def test_reclaim_marks_an_expired_lease() -> None:
    subject, clock = service(ttl=60)
    lease = await acquire(subject)
    clock[0] = START + timedelta(seconds=120)

    expired = await subject.expired()
    assert [item.lease_id for item in expired] == [lease.lease_id]

    reclaimed = await subject.mark_reclaimed("tenant-a", lease.lease_id)
    assert reclaimed.state is SandboxLeaseState.RECLAIMED
    assert reclaimed.reclaimed_at == clock[0]
    assert await subject.expired() == []


@pytest.mark.asyncio
async def test_renewing_a_released_lease_is_refused() -> None:
    subject, _ = service()
    lease = await acquire(subject)
    await subject.release("tenant-a", lease.lease_id, epoch=lease.epoch)
    with pytest.raises(SandboxLeaseError, match="released"):
        await subject.renew("tenant-a", lease.lease_id)


@pytest.mark.asyncio
async def test_an_empty_identity_is_rejected() -> None:
    subject, _ = service()
    with pytest.raises(ValueError, match="must not be empty"):
        await acquire(subject, owner=" ")
    with pytest.raises(ValueError, match="sandbox id"):
        await acquire(subject, sandbox_id="")


@pytest.mark.asyncio
async def test_a_terminal_run_lease_is_reconciled_to_released() -> None:
    """run 已终态 + lease 仍 live → 一次对账回收为 released，且可重复（幂等）。"""
    subject, clock = service()
    await acquire(subject)
    clock[0] = START + timedelta(seconds=10)
    released = await subject.reconcile_dead_run_lease(
        "tenant-a", "session-1", run_is_terminal=_terminal
    )

    assert released is not None
    assert released.state is SandboxLeaseState.RELEASED
    assert released.released_at == clock[0]
    assert [item.state for item in await subject.live("tenant-a")] == []
    # 幂等：第二次对账没有可回收对象。
    assert (
        await subject.reconcile_dead_run_lease("tenant-a", "session-1", run_is_terminal=_terminal)
        is None
    )
    # 回收后同会话可立即再取，epoch 递增。
    second = await acquire(subject, run_id="run-2", owner="worker-2", sandbox_id="sandbox-2")
    assert second.epoch == 2


@pytest.mark.asyncio
async def test_a_live_run_lease_is_never_reconciled() -> None:
    """终态外的 live lease 不得误回收。"""
    subject, _ = service()
    await acquire(subject)
    assert (
        await subject.reconcile_dead_run_lease("tenant-a", "session-1", run_is_terminal=_running)
        is None
    )
    live = await subject.live("tenant-a")
    assert [item.state for item in live] == [SandboxLeaseState.ACTIVE]


@pytest.mark.asyncio
async def test_an_unverifiable_run_is_never_reconciled() -> None:
    """查询失败的 Run（无法核实归属）不允许被猜测回收。"""
    subject, _ = service()
    await acquire(subject)

    async def broken(_run_id: str) -> bool:
        raise RuntimeError("run store unavailable")

    with pytest.raises(RuntimeError, match="run store unavailable"):
        await subject.reconcile_dead_run_lease("tenant-a", "session-1", run_is_terminal=broken)
    assert [item.state for item in await subject.live("tenant-a")] == [SandboxLeaseState.ACTIVE]


@pytest.mark.asyncio
async def test_another_sessions_lease_is_out_of_scope() -> None:
    subject, _ = service()
    await acquire(subject)
    assert (
        await subject.reconcile_dead_run_lease("tenant-a", "session-2", run_is_terminal=_terminal)
        is None
    )


async def _terminal(_run_id: str) -> bool:
    return True


async def _running(_run_id: str) -> bool:
    return False
