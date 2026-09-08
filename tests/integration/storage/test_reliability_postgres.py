import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from harness.core.models import Run, RunStatus
from harness.reliability.models import CapacitySnapshot, IncidentStatus, ReliabilityIncident
from harness.storage.database import SessionFactory, create_database, create_schema, drop_schema
from harness.storage.reliability_repository import PostgresReliabilityRepository
from harness.storage.repositories import PostgresRunRepository

NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest_asyncio.fixture
async def reliability_database() -> AsyncIterator[SessionFactory]:
    engine, sessions = create_database(DATABASE_URL)
    await drop_schema(engine)
    await create_schema(engine)
    try:
        yield sessions
    finally:
        await engine.dispose()


def incident(incident_id: str) -> ReliabilityIncident:
    return ReliabilityIncident(
        tenantId="tenant-a",
        incidentId=incident_id,
        fingerprint="reaper-finalize:run-1",
        kind="reaper_finalize_failed",
        severity="critical",
        status=IncidentStatus.OPEN,
        resourceType="run",
        resourceId="run-1",
        summary="repair",
        openedAt=NOW,
        updatedAt=NOW,
    )


@pytest.mark.asyncio
async def test_incident_upsert_and_recovery_claim_are_concurrency_safe(
    reliability_database: SessionFactory,
) -> None:
    repository = PostgresReliabilityRepository(reliability_database)

    stored = await asyncio.gather(
        repository.upsert_incident(incident("incident-a")),
        repository.upsert_incident(incident("incident-b")),
    )

    assert stored[0].incident_id == stored[1].incident_id
    claims = await asyncio.gather(
        repository.try_claim_incident(
            "tenant-a",
            "reaper-finalize:run-1",
            owner="worker-a",
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        ),
        repository.try_claim_incident(
            "tenant-a",
            "reaper-finalize:run-1",
            owner="worker-b",
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(seconds=30),
        ),
    )
    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    assert winners[0].recovery_attempts == 1


@pytest.mark.asyncio
async def test_stale_run_query_uses_durable_updated_at_and_fencing(
    reliability_database: SessionFactory,
) -> None:
    repository = PostgresRunRepository(reliability_database)
    stale = Run(
        run_id="run-stale",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="stale",
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW - timedelta(hours=2),
    )
    fresh = stale.model_copy(
        update={
            "run_id": "run-fresh",
            "idempotency_key": "fresh",
            "updated_at": NOW,
        }
    )
    await repository.add(stale)
    await repository.add(fresh)

    candidates = await repository.list_stale(
        frozenset({RunStatus.RUNNING}), NOW - timedelta(hours=1), limit=10
    )
    assert [item.run_id for item in candidates] == ["run-stale"]

    timed_out = stale.model_copy(
        update={
            "status": RunStatus.TIMED_OUT,
            "updated_at": NOW,
            "fencing_token": 1,
        }
    )
    assert await repository.compare_and_set(RunStatus.RUNNING, timed_out)
    assert not await repository.compare_and_set(RunStatus.RUNNING, timed_out)


@pytest.mark.asyncio
async def test_capacity_snapshots_are_tenant_scoped(
    reliability_database: SessionFactory,
) -> None:
    repository = PostgresReliabilityRepository(reliability_database)

    def snapshot(tenant_id: str) -> CapacitySnapshot:
        return CapacitySnapshot(
            tenantId=tenant_id,
            snapshotId="snapshot-1",
            capturedAt=NOW,
            queueReady=0,
            queueProcessing=0,
            runsByStatus={},
            stuckRunsByStatus={},
            activePreviews=0,
            pendingApprovals=0,
            artifactBytes=0,
            snapshotBytes=0,
            lifecycleBacklog=0,
            credentialLeases=0,
        )

    await repository.save_capacity(snapshot("tenant-a"))
    await repository.save_capacity(snapshot("tenant-b"))

    tenant_a = await repository.latest_capacity("tenant-a")
    tenant_b = await repository.latest_capacity("tenant-b")
    assert tenant_a is not None and tenant_a.tenant_id == "tenant-a"
    assert tenant_b is not None and tenant_b.tenant_id == "tenant-b"
