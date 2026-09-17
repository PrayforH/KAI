"""Sandbox instance governance: untracked sandboxes and stale leases."""

from datetime import UTC, datetime, timedelta

import pytest

from harness.sandbox.governance import SandboxGovernanceService, SandboxInstance
from harness.sandbox.lease import SandboxLeaseService
from harness.storage.sandbox_lease_repository import InMemorySandboxLeaseRepository

START = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)


def ids(prefix: str) -> str:
    ids.counter = getattr(ids, "counter", 0) + 1  # type: ignore[attr-defined]
    return f"{prefix}-{ids.counter}"  # type: ignore[attr-defined]


class FakeProvider:
    provider_name = "opensandbox-deferred"

    def __init__(self, instances: list[str], *, created_at: datetime | None = None) -> None:
        self.instances = list(instances)
        self.created_at = created_at
        self.reclaimed: list[str] = []

    async def inventory(self) -> list[SandboxInstance]:
        return [
            SandboxInstance(sandbox_id=item, created_at=self.created_at)
            for item in self.instances
        ]

    async def platform_version(self) -> str:
        return "0.2.3"

    async def reclaim(self, sandbox_id: str) -> bool:
        self.reclaimed.append(sandbox_id)
        self.instances = [item for item in self.instances if item != sandbox_id]
        return True


class PlainProvider:
    provider_name = "local"


def service(
    provider: object, *, ttl: int = 60
) -> tuple[SandboxGovernanceService, SandboxLeaseService, list[datetime]]:
    clock = [START]
    leases = SandboxLeaseService(
        InMemorySandboxLeaseRepository(),
        clock=lambda: clock[0],
        id_generator=ids,
        default_ttl_seconds=ttl,
    )
    return (
        SandboxGovernanceService(leases, provider, clock=lambda: clock[0]),  # pyright: ignore[reportArgumentType]
        leases,
        clock,
    )


@pytest.mark.asyncio
async def test_a_provider_without_inventory_is_left_alone() -> None:
    governance, _, _ = service(PlainProvider())
    assert governance.can_reconcile is False
    report = await governance.report()
    assert report.platform_instances is None
    assert report.untracked == ()
    assert report.missing == ()


@pytest.mark.asyncio
async def test_report_separates_untracked_from_missing() -> None:
    provider = FakeProvider(["sandbox-tracked", "sandbox-orphan"])
    governance, leases, _ = service(provider)
    await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-1",
        run_id="run-1",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-tracked",
    )
    await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-2",
        run_id="run-2",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-gone",
    )

    report = await governance.report()

    assert report.provider == "opensandbox-deferred"
    assert report.platform_version == "0.2.3"
    assert report.live_leases == 2
    assert report.platform_instances == 2
    assert report.untracked == ("sandbox-orphan",)
    assert report.missing == ("sandbox-gone",)
    assert provider.reclaimed == []


@pytest.mark.asyncio
async def test_reclaim_destroys_only_expired_untracked_sandboxes() -> None:
    provider = FakeProvider(["sandbox-orphan", "sandbox-live"])
    governance, leases, clock = service(provider)
    orphan = await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-1",
        run_id="run-1",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-orphan",
    )
    live = await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-2",
        run_id="run-2",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-live",
    )
    clock[0] = START + timedelta(seconds=90)
    # The Session that is still alive renewed before the sweep, so only the
    # abandoned sandbox is past its lease.
    await leases.renew("tenant-a", live.lease_id, epoch=live.epoch)

    report = await governance.reclaim_orphans()

    assert provider.reclaimed == ["sandbox-orphan"]
    assert report.reclaimed == ("sandbox-orphan",)
    assert (await leases.for_run("tenant-a", "run-1")) == orphan.model_copy(
        update={
            "state": orphan.state.RECLAIMED,
            "reclaimed_at": clock[0],
        }
    )
    stored_live = await leases.for_run("tenant-a", "run-2")
    assert stored_live is not None and stored_live.lease_id == live.lease_id
    assert stored_live.state.value == "active"


@pytest.mark.asyncio
async def test_a_stale_lease_is_closed_when_its_sandbox_vanished() -> None:
    provider = FakeProvider([])
    governance, leases, _ = service(provider)
    await leases.acquire(
        tenant_id="tenant-a",
        session_id="session-1",
        run_id="run-1",
        owner="worker-1",
        provider="opensandbox-deferred",
        sandbox_id="sandbox-gone",
    )

    report = await governance.reclaim_orphans()

    assert report.missing == ("sandbox-gone",)
    stored = await leases.for_run("tenant-a", "run-1")
    assert stored is not None and stored.state.value == "reclaimed"
    assert await leases.live("tenant-a") == []


@pytest.mark.asyncio
async def test_a_failing_reclaim_does_not_stop_the_sweep() -> None:
    provider = FakeProvider(["sandbox-orphan", "sandbox-other"])
    governance, leases, clock = service(provider)
    for index, sandbox_id in enumerate(("sandbox-orphan", "sandbox-other")):
        await leases.acquire(
            tenant_id="tenant-a",
            session_id=f"session-{index}",
            run_id=f"run-{index}",
            owner="worker-1",
            provider="opensandbox-deferred",
            sandbox_id=sandbox_id,
        )
    clock[0] = START + timedelta(seconds=90)

    calls: list[str] = []

    async def flaky(sandbox_id: str) -> bool:
        calls.append(sandbox_id)
        if sandbox_id == "sandbox-orphan":
            raise RuntimeError("platform hiccup")
        return True

    provider.reclaim = flaky  # type: ignore[method-assign]
    report = await governance.reclaim_orphans()

    assert calls == ["sandbox-orphan", "sandbox-other"]
    assert report.reclaimed == ("sandbox-other",)


@pytest.mark.asyncio
async def test_a_stray_sandbox_without_any_lease_is_reclaimed_after_the_ttl() -> None:
    """A probe or crashed create leaves no lease behind; age is the only evidence."""

    provider = FakeProvider(["sandbox-stray"], created_at=START)
    governance, _, clock = service(provider, ttl=60)

    clock[0] = START + timedelta(seconds=30)
    fresh = await governance.reclaim_orphans()
    assert fresh.reclaimed == ()
    assert provider.reclaimed == []

    clock[0] = START + timedelta(seconds=61)
    stale = await governance.reclaim_orphans()
    assert stale.reclaimed == ("sandbox-stray",)
    assert provider.reclaimed == ["sandbox-stray"]


@pytest.mark.asyncio
async def test_a_stray_sandbox_without_a_creation_time_is_left_alone() -> None:
    provider = FakeProvider(["sandbox-unknown"], created_at=None)
    governance, _, clock = service(provider, ttl=60)
    clock[0] = START + timedelta(days=1)

    report = await governance.reclaim_orphans()

    assert report.untracked == ("sandbox-unknown",)
    assert report.reclaimed == ()
    assert provider.reclaimed == []
