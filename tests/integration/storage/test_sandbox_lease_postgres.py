"""The lease compare-and-set has to decide in the write, not before it.

Several workers share one session's sandbox, and the lease is the only durable record
of who owns it. The fencing epoch exists so a holder whose lease moved on can detect
that before touching the sandbox — which only holds if the epoch travels with the
write. A read-then-write can be overtaken between the two steps and still win.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from harness.core.errors import NotFoundError
from harness.sandbox.lease import SandboxLease, SandboxLeaseState
from harness.storage.sandbox_lease_repository import PostgresSandboxLeaseRepository

NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _lease(*, epoch: int, state: SandboxLeaseState = SandboxLeaseState.ACTIVE) -> SandboxLease:
    return SandboxLease(
        lease_id="lease-1",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        owner="worker-1",
        epoch=epoch,
        provider="docker",
        sandbox_id="sandbox-1",
        state=state,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        renewed_at=NOW,
    )


@pytest.mark.asyncio
async def test_replace_advances_the_epoch_only_for_the_current_holder(database) -> None:
    _, sessions = database
    repository = PostgresSandboxLeaseRepository(sessions)
    await repository.add(_lease(epoch=1))

    assert await repository.replace(_lease(epoch=2), expected_epoch=1) is True
    assert (await repository.get("tenant-a", "lease-1")).epoch == 2

    # A stale holder — a worker whose lease was already reclaimed — must not win.
    stale = await repository.replace(
        _lease(epoch=3, state=SandboxLeaseState.RECLAIMED), expected_epoch=1
    )
    assert stale is False
    stored = await repository.get("tenant-a", "lease-1")
    assert stored.epoch == 2 and stored.state is SandboxLeaseState.ACTIVE


@pytest.mark.asyncio
async def test_replace_decides_in_the_update_instead_of_a_read(database, monkeypatch) -> None:
    """The epoch comparison must be part of the statement that writes.

    The interleaving that separates the two shapes — a second holder advancing the
    lease in the window between a first holder's read and its write — cannot be forced
    deterministically through the public API without re-implementing the repository in
    the test. What can be asserted is the shape that closes that window: one
    conditional UPDATE and no pre-flight read of the lease.
    """

    _, sessions = database
    repository = PostgresSandboxLeaseRepository(sessions)
    await repository.add(_lease(epoch=1))

    statements: list[str] = []
    real_execute = AsyncSession.execute

    async def recording_execute(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
        statements.append(" ".join(str(statement).split()))
        return await real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", recording_execute)
    assert await repository.replace(_lease(epoch=2), expected_epoch=1) is True

    assert len(statements) == 1, statements
    statement = statements[0]
    assert statement.startswith("UPDATE"), statement
    # ``epoch = :epoch_1`` is the bound expectation in the WHERE clause; the SET
    # assignment above it uses the row's new value, so the parameter name is the tell.
    assert "sandbox_leases.epoch = :epoch_1" in statement, statement


@pytest.mark.asyncio
async def test_replace_reports_a_missing_lease_as_not_found(database) -> None:
    _, sessions = database
    repository = PostgresSandboxLeaseRepository(sessions)

    with pytest.raises(NotFoundError):
        await repository.replace(_lease(epoch=1), expected_epoch=1)
