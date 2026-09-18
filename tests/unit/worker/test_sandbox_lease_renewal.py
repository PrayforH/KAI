"""The worker heartbeat keeps a Run's durable sandbox lease alive."""

from datetime import UTC, datetime

import pytest

from harness.core.ports import RunTask
from harness.sandbox.lease import SandboxLeaseError, SandboxLeaseService, SandboxLeaseState
from harness.storage.sandbox_lease_repository import InMemorySandboxLeaseRepository
from harness.worker.main import _renew_sandbox_lease

NOW = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)


def ids(prefix: str) -> str:
    ids.counter = getattr(ids, "counter", 0) + 1  # type: ignore[attr-defined]
    return f"{prefix}-{ids.counter}"  # type: ignore[attr-defined]


def service() -> SandboxLeaseService:
    return SandboxLeaseService(
        InMemorySandboxLeaseRepository(),
        clock=lambda: NOW,
        id_generator=ids,
        default_ttl_seconds=60,
    )


def task() -> RunTask:
    return RunTask(tenant_id="tenant-a", run_id="run-1", session_id="session-1", attempts=1)


@pytest.mark.asyncio
async def test_renewal_extends_the_active_lease() -> None:
    leases = service()
    lease = await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-1",
        run_id="run-1",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-1",
    )

    await _renew_sandbox_lease(leases, task())

    renewed = await leases.for_run("tenant-a", "run-1")
    assert renewed is not None and renewed.lease_id == lease.lease_id
    assert renewed.state is SandboxLeaseState.ACTIVE


@pytest.mark.asyncio
async def test_renewal_is_a_no_op_without_a_lease() -> None:
    leases = service()
    await _renew_sandbox_lease(leases, task())
    assert await leases.live("tenant-a") == []


@pytest.mark.asyncio
async def test_renewal_never_raises_into_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leases = service()
    await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-1",
        run_id="run-1",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-1",
    )

    async def failing(*_args: object, **_kwargs: object) -> None:
        raise SandboxLeaseError("database is down")

    monkeypatch.setattr(leases, "renew", failing)
    await _renew_sandbox_lease(leases, task())
