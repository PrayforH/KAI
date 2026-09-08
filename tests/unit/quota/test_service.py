import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.core.errors import ConflictError
from harness.quota.models import (
    CostState,
    QuotaResource,
    QuotaScope,
    ReplaceQuotaPolicyRequest,
    ReservationState,
)
from harness.quota.repositories import InMemoryQuotaRepository, QuotaExceededError
from harness.quota.service import QuotaService

NOW = datetime(2026, 7, 16, 4, 5, tzinfo=UTC)


def service() -> tuple[QuotaService, InMemoryQuotaRepository, list[datetime]]:
    repository = InMemoryQuotaRepository()
    clock = [NOW]
    sequence = 0

    def ids(prefix: str) -> str:
        nonlocal sequence
        sequence += 1
        return f"{prefix}-{sequence}"

    return (
        QuotaService(repository, clock=lambda: clock[0], id_generator=ids),
        repository,
        clock,
    )


async def policy(
    quotas: QuotaService,
    *,
    policy_id: str,
    limits: dict[QuotaResource, int],
    scope: QuotaScope | None = None,
    alert_thresholds: dict[QuotaResource, int] | None = None,
) -> None:
    await quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner",
        policy_id=policy_id,
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0, scope=scope or QuotaScope(), limits=limits
            , alertThresholds=alert_thresholds or {}
        ),
    )


@pytest.mark.asyncio
async def test_atomic_concurrent_reservation_never_oversells() -> None:
    quotas, repository, _ = service()
    await policy(
        quotas,
        policy_id="tenant-default",
        limits={QuotaResource.CONCURRENT_RUNS: 3},
    )

    results = await asyncio.gather(
        *(
            quotas.reserve(
                tenant_id="tenant-a",
                resource=QuotaResource.CONCURRENT_RUNS,
                amount=1,
                subject_id=f"run-{index}",
                idempotency_key=f"run-{index}",
            )
            for index in range(10)
        ),
        return_exceptions=True,
    )

    admitted = [item for item in results if not isinstance(item, Exception)]
    rejected = [item for item in results if isinstance(item, QuotaExceededError)]
    assert len(admitted) == 3
    assert len(rejected) == 7
    counter = (await repository.list_counters("tenant-a"))[0]
    assert counter.reserved == 3
    assert counter.committed == 0


@pytest.mark.asyncio
async def test_hierarchical_constraints_apply_tenant_and_agent_limits() -> None:
    quotas, _, _ = service()
    await policy(
        quotas,
        policy_id="tenant-default",
        limits={QuotaResource.CONCURRENT_RUNS: 4},
    )
    await policy(
        quotas,
        policy_id="agent-research",
        scope=QuotaScope(agentName="research"),
        limits={QuotaResource.CONCURRENT_RUNS: 1},
    )
    first = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.CONCURRENT_RUNS,
        amount=1,
        subject_id="run-1",
        idempotency_key="run-1",
        agent_name="research",
    )

    with pytest.raises(QuotaExceededError) as failure:
        await quotas.reserve(
            tenant_id="tenant-a",
            resource=QuotaResource.CONCURRENT_RUNS,
            amount=1,
            subject_id="run-2",
            idempotency_key="run-2",
            agent_name="research",
        )

    assert {item.scope_key for item in first.constraints} == {
        "agent=*|environment=*",
        "agent=research|environment=*",
    }
    assert failure.value.limit == 1


@pytest.mark.asyncio
async def test_all_matching_organization_team_user_agent_environment_and_key_scopes_apply() -> None:
    quotas, _, _ = service()
    scopes = (
        QuotaScope(organizationId="tenant-a"),
        QuotaScope(teamId="risk"),
        QuotaScope(userId="analyst"),
        QuotaScope(agentName="research"),
        QuotaScope(environment="production"),
        QuotaScope(apiKeyId="trigger-nightly"),
    )
    for index, scope in enumerate(scopes):
        await policy(
            quotas,
            policy_id=f"scope-{index}",
            scope=scope,
            limits={QuotaResource.CONCURRENT_RUNS: 10},
        )

    reservation = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.CONCURRENT_RUNS,
        amount=1,
        subject_id="run-scoped",
        idempotency_key="run-scoped",
        organization_id="tenant-a",
        team_ids=("risk",),
        user_id="analyst",
        agent_name="research",
        environment="production",
        api_key_id="trigger-nightly",
    )

    assert len(reservation.constraints) == 7
    assert any("team=risk" in item.scope_key for item in reservation.constraints)
    assert any("user=analyst" in item.scope_key for item in reservation.constraints)
    assert any("key=trigger-nightly" in item.scope_key for item in reservation.constraints)


@pytest.mark.asyncio
async def test_budget_alert_is_deterministic_for_scope_resource_window_threshold() -> None:
    quotas, _, _ = service()
    await policy(
        quotas,
        policy_id="tenant-default",
        limits={QuotaResource.ARTIFACT_BYTES: 100},
        alert_thresholds={QuotaResource.ARTIFACT_BYTES: 75},
    )
    reservation = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.ARTIFACT_BYTES,
        amount=80,
        subject_id="run-alert",
        idempotency_key="run-alert",
    )
    await quotas.commit(reservation, amount=80)

    first = (await quotas.usage("tenant-a")).alerts
    second = (await quotas.usage("tenant-a")).alerts

    assert first == second
    assert len(first) == 1
    assert first[0].usage_percent == 80
    assert first[0].threshold_percent == 75


@pytest.mark.asyncio
async def test_usage_is_aggregated_for_tenant_agent_environment_and_combined_scopes() -> None:
    quotas, _, _ = service()
    for policy_id, scope, limit in (
        ("tenant-default", QuotaScope(), 1000),
        ("agent-research", QuotaScope(agentName="research"), 800),
        ("environment-production", QuotaScope(environment="production"), 700),
        (
            "research-production",
            QuotaScope(agentName="research", environment="production"),
            600,
        ),
    ):
        await policy(
            quotas,
            policy_id=policy_id,
            scope=scope,
            limits={QuotaResource.ARTIFACT_BYTES: limit},
        )
    reservation = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.ARTIFACT_BYTES,
        amount=100,
        subject_id="run-aggregate",
        idempotency_key="run-aggregate:tokens",
        agent_name="research",
        environment="production",
    )

    await quotas.commit(reservation, amount=40)

    counters = {
        counter.scope_key: counter
        for counter in (await quotas.usage("tenant-a")).counters
    }
    assert set(counters) == {
        "agent=*|environment=*",
        "agent=research|environment=*",
        "agent=*|environment=production",
        "agent=research|environment=production",
    }
    assert {counter.committed for counter in counters.values()} == {40}
    assert {counter.reserved for counter in counters.values()} == {0}


@pytest.mark.asyncio
async def test_reserve_commit_release_are_idempotent_and_partial_commit_is_exact() -> None:
    quotas, repository, _ = service()
    reservation = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.ARTIFACT_BYTES,
        amount=2_000_000,
        subject_id="run-1",
        idempotency_key="run-1-cost",
    )
    repeated = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.ARTIFACT_BYTES,
        amount=2_000_000,
        subject_id="run-1",
        idempotency_key="run-1-cost",
    )

    assert repeated.reservation_id == reservation.reservation_id
    committed = await quotas.commit(reservation, amount=250_000)
    assert (await quotas.commit(committed, amount=250_000)).state is ReservationState.COMMITTED
    counter = (await repository.list_counters("tenant-a"))[0]
    assert counter.reserved == 0
    assert counter.committed == 250_000
    assert len(await repository.list_ledger("tenant-a")) == 1


@pytest.mark.asyncio
async def test_worker_admission_recovers_all_run_reservations_idempotently() -> None:
    quotas, _, _ = service()

    first = await quotas.ensure_run_admitted(
        tenant_id="tenant-a",
        run_id="run-worker",
        agent_name="agent-a",
        environment="production",
        max_budget_usd=2,
        max_model_tokens=200_000,
        ttl_seconds=60,
    )
    repeated = await quotas.ensure_run_admitted(
        tenant_id="tenant-a",
        run_id="run-worker",
        agent_name="agent-a",
        environment="production",
        max_budget_usd=2,
        max_model_tokens=200_000,
        ttl_seconds=60,
    )

    assert {item.resource for item in first} == {
        QuotaResource.CONCURRENT_RUNS,
    }
    assert [item.reservation_id for item in repeated] == [
        item.reservation_id for item in first
    ]


@pytest.mark.asyncio
async def test_actual_usage_over_reservation_is_recorded_instead_of_lost() -> None:
    quotas, repository, _ = service()
    reservation = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.ARTIFACT_BYTES,
        amount=10,
        subject_id="run-overage",
        idempotency_key="run-overage:tokens",
    )

    await quotas.commit(reservation, amount=12)

    counter = (await repository.list_counters("tenant-a"))[0]
    ledger = (await repository.list_ledger("tenant-a"))[0]
    assert counter.reserved == 0
    assert counter.committed == 12
    assert ledger.amount == 12


@pytest.mark.asyncio
async def test_missing_cost_is_unknown_and_never_coerced_to_zero() -> None:
    quotas, repository, _ = service()
    await quotas.admit_run(
        tenant_id="tenant-a",
        run_id="run-1",
        agent_name="agent-a",
        environment="production",
        max_budget_usd=2,
        max_model_tokens=200_000,
        ttl_seconds=60,
    )

    await quotas.record_run_result(
        tenant_id="tenant-a",
        run_id="run-1",
        agent_name="agent-a",
        environment="production",
        usage=None,
        total_cost_usd=None,
    )

    ledger = await repository.list_ledger("tenant-a")
    unknown = next(item for item in ledger if item.cost_state is CostState.UNKNOWN)
    assert unknown.amount is None
    view = await quotas.usage("tenant-a")
    assert view.unknown_cost_entries == 1


@pytest.mark.asyncio
async def test_expired_and_cancelled_subject_reservations_are_released() -> None:
    quotas, repository, clock = service()
    reservation = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.CONCURRENT_RUNS,
        amount=1,
        subject_id="run-expired",
        idempotency_key="run-expired",
        ttl_seconds=10,
    )
    clock[0] += timedelta(seconds=11)

    assert await quotas.reap_expired("tenant-a") == 1
    assert (
        await repository.get_reservation("tenant-a", reservation.idempotency_key)
    ).state is ReservationState.EXPIRED  # type: ignore[union-attr]
    assert (await repository.list_counters("tenant-a"))[0].reserved == 0

    active = await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.CONCURRENT_RUNS,
        amount=1,
        subject_id="run-cancelled",
        idempotency_key="run-cancelled",
    )
    assert await quotas.release_subject("tenant-a", "run-cancelled") == 1
    assert (
        await repository.get_reservation("tenant-a", active.idempotency_key)
    ).state is ReservationState.RELEASED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_policy_replace_requires_matching_revision() -> None:
    quotas, _, _ = service()
    await policy(
        quotas,
        policy_id="tenant-default",
        limits={QuotaResource.CONCURRENT_RUNS: 2},
    )

    with pytest.raises(ConflictError, match="revision changed"):
        await quotas.replace_policy(
            tenant_id="tenant-a",
            user_id="owner",
            policy_id="tenant-default",
            request=ReplaceQuotaPolicyRequest(
                expectedRevision=0,
                limits={QuotaResource.CONCURRENT_RUNS: 3},
            ),
        )

    effective = (await quotas.list_policies("tenant-a"))[0]
    assert effective.limits[QuotaResource.CONCURRENT_RUNS] == 2
    assert effective.limits[QuotaResource.ARTIFACT_BYTES] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource", [QuotaResource.MODEL_TOKENS, QuotaResource.MODEL_COST_MICRO_USD]
)
async def test_legacy_model_limits_never_block_any_agent(resource: QuotaResource) -> None:
    quotas, repository, _ = service()
    for scope_id, scope in [("tenant", QuotaScope()), ("agent", QuotaScope(agentName="research"))]:
        await policy(quotas, policy_id=scope_id, scope=scope, limits={resource: 1})
    for agent in ("research", "other-agent"):
        reservation = await quotas.consume(
            tenant_id="tenant-a", resource=resource, amount=1_000_000_000,
            subject_id=agent, idempotency_key=agent, agent_name=agent,
        )
        assert reservation.constraints == ()
    ledger = await repository.list_ledger("tenant-a")
    assert sum(entry.amount or 0 for entry in ledger) == 2_000_000_000
