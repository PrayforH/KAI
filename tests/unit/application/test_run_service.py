from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.adapters.memory import (
    InMemoryCancellationWakeup,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryTaskQueue,
)
from harness.application.events import EventService
from harness.application.runs import RunQuotaPlan, RunService, apply_environment_quota
from harness.config import Settings
from harness.core.models import RunStatus, Session
from harness.deployments.models import (
    EnvironmentName,
    EnvironmentPolicySnapshot,
    EnvironmentQuotaBoundary,
    EnvironmentResourcePolicy,
)
from harness.observability.provider import build_observability

NOW = datetime(2026, 7, 11, tzinfo=UTC)


class FailingCancellationWakeup:
    async def publish(self, tenant_id: str, run_id: str, fencing_token: int) -> None:
        raise ConnectionError("redis unavailable")

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_fencing_token: int,
        *,
        timeout_seconds: float,
    ) -> bool:
        raise ConnectionError("redis unavailable")


def id_generator() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return generate


def test_environment_snapshot_cannot_reintroduce_operational_limits() -> None:
    policy = EnvironmentResourcePolicy(
        quota=EnvironmentQuotaBoundary(
            maxRunBudgetUsd=0.4,
            maxModelTokens=80_000,
            maxArtifactBytes=4_096,
        )
    )
    snapshot = EnvironmentPolicySnapshot(
        environment=EnvironmentName.PRODUCTION,
        environmentRevision=3,
        policyRevision=2,
        policyHash=policy.digest(),
        resourcePolicy=policy,
        capturedAt=NOW,
    )
    session = Session(
        session_id="session-policy",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
        environment="production",
        deployment_snapshot_id="snapshot-1",
        environment_snapshot=snapshot.model_dump(mode="json", by_alias=True),
    )

    result = apply_environment_quota(
        RunQuotaPlan(
            max_budget_usd=1.0,
            max_model_tokens=200_000,
            ttl_seconds=1_200,
        ),
        session,
    )

    assert result == RunQuotaPlan(
        max_budget_usd=None,
        max_model_tokens=None,
        ttl_seconds=1_200,
    )


@pytest.mark.asyncio
async def test_create_run_is_idempotent_and_queues_once() -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    queue = InMemoryTaskQueue()
    events = InMemoryEventRepository()
    bus = InMemoryEventBus()
    ids = id_generator()
    await sessions.add(
        Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
    )
    service = RunService(
        sessions,
        runs,
        queue,
        EventService(events, bus, clock=lambda: NOW, id_generator=ids),
        clock=lambda: NOW,
        id_generator=ids,
    )

    first = await service.create("tenant-a", "session-1", "idem-1")
    second = await service.create("tenant-a", "session-1", "idem-1")

    assert first == second
    assert first.status is RunStatus.QUEUED
    assert (await queue.dequeue()).run_id == first.run_id  # type: ignore[union-attr]
    assert await queue.dequeue() is None
    stored_events = await events.list_after("tenant-a", first.run_id, 0)
    assert [(item.sequence, item.type) for item in stored_events] == [(1, "run.queued")]


@pytest.mark.asyncio
async def test_create_run_reuses_same_active_prompt_and_attachments() -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    queue = InMemoryTaskQueue()
    events = InMemoryEventRepository()
    ids = id_generator()
    await sessions.add(
        Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
    )
    service = RunService(
        sessions,
        runs,
        queue,
        EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids),
        clock=lambda: NOW,
        id_generator=ids,
    )

    first = await service.create_with_result(
        "tenant-a",
        "session-1",
        "client-1",
        input={"prompt": "生成 PPT", "input_artifact_ids": ["b", "a"]},
        deduplicate_active_input=True,
    )
    duplicate = await service.create_with_result(
        "tenant-a",
        "session-1",
        "client-2",
        input={"prompt": "生成 PPT", "input_artifact_ids": ["a", "b"]},
        deduplicate_active_input=True,
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.deduplicated is True
    assert duplicate.run == first.run
    assert (await queue.dequeue()).run_id == first.run.run_id  # type: ignore[union-attr]
    assert await queue.dequeue() is None


@pytest.mark.asyncio
async def test_queued_run_records_waiting_approval_predecessor() -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    queue = InMemoryTaskQueue()
    events = InMemoryEventRepository()
    ids = id_generator()
    await sessions.add(
        Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
    )
    service = RunService(
        sessions,
        runs,
        queue,
        EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids),
        clock=lambda: NOW,
        id_generator=ids,
    )
    predecessor = await service.create(
        "tenant-a",
        "session-1",
        "client-1",
        input={"prompt": "先处理", "input_artifact_ids": []},
        deduplicate_active_input=True,
    )
    waiting = predecessor.model_copy(
        update={
            "status": RunStatus.WAITING_APPROVAL,
            "fencing_token": predecessor.fencing_token + 1,
        }
    )
    assert await runs.compare_and_set(RunStatus.QUEUED, waiting)

    queued = await service.create(
        "tenant-a",
        "session-1",
        "client-2",
        input={"prompt": "另一个任务", "input_artifact_ids": []},
        deduplicate_active_input=True,
    )

    queued_events = await events.list_after("tenant-a", queued.run_id, 0)
    assert queued_events[0].payload == {
        "reason_code": "predecessor_waiting_approval",
        "reason": "前序任务等待审批",
        "blocked_by_run_id": predecessor.run_id,
        "blocked_by_status": "waiting_approval",
    }


@pytest.mark.asyncio
async def test_create_run_annotates_the_api_trace_with_session_identity() -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    queue = InMemoryTaskQueue()
    events = InMemoryEventRepository()
    bus = InMemoryEventBus()
    ids = id_generator()
    exporter = InMemorySpanExporter()
    observability = build_observability(
        Settings(
            otel_enabled=True,
            otlp_endpoint="http://unused/v1/traces",
            otel_content_capture="redacted",
        ),
        exporter=exporter,
        processor_factory=SimpleSpanProcessor,
    )
    await sessions.add(
        Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
    )
    service = RunService(
        sessions,
        runs,
        queue,
        EventService(events, bus, clock=lambda: NOW, id_generator=ids),
        clock=lambda: NOW,
        id_generator=ids,
        observability=observability,
    )

    with observability.span("harness.api.request"):
        run = await service.create(
            "tenant-a",
            "session-1",
            "idem-1",
            input={"prompt": "用户问题 token=private-value"},
        )

    span = exporter.get_finished_spans()[0]
    assert span.attributes is not None
    assert span.attributes["langfuse.session.id"] == "session-1"
    assert span.attributes["langfuse.trace.metadata.run_id"] == run.run_id
    assert span.attributes["session.id"] == "session-1"
    assert span.attributes["run.id"] == run.run_id
    assert span.attributes["langfuse.trace.input"] == ("用户问题 token=[REDACTED]")
    assert "traceparent" in run.trace_context


@pytest.mark.asyncio
async def test_cancel_queued_run_reaches_cancelled_without_worker_owner() -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    queue = InMemoryTaskQueue()
    events = InMemoryEventRepository()
    bus = InMemoryEventBus()
    ids = id_generator()
    await sessions.add(
        Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
    )
    service = RunService(
        sessions,
        runs,
        queue,
        EventService(events, bus, clock=lambda: NOW, id_generator=ids),
        clock=lambda: NOW,
        id_generator=ids,
        cancellation_wakeup=FailingCancellationWakeup(),
    )
    run = await service.create("tenant-a", "session-1", "idem-1")

    cancelled = await service.cancel("tenant-a", run.run_id)

    assert cancelled.status is RunStatus.CANCELLED
    stored_events = await events.list_after("tenant-a", run.run_id, 0)
    assert [(item.sequence, item.type) for item in stored_events] == [
        (1, "run.queued"),
        (2, "run.cancelling"),
        (3, "run.cancelled"),
    ]

    assert await service.cancel("tenant-a", run.run_id) == cancelled


@pytest.mark.asyncio
async def test_cancel_active_run_waits_for_worker_terminal_and_is_idempotent() -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    queue = InMemoryTaskQueue()
    events = InMemoryEventRepository()
    cancellation_wakeup = InMemoryCancellationWakeup()
    ids = id_generator()
    await sessions.add(
        Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
    )
    service = RunService(
        sessions,
        runs,
        queue,
        EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids),
        clock=lambda: NOW,
        id_generator=ids,
        cancellation_wakeup=cancellation_wakeup,
    )
    queued = await service.create("tenant-a", "session-1", "idem-1")
    running = queued.model_copy(
        update={
            "status": RunStatus.RUNNING,
            "fencing_token": queued.fencing_token + 1,
        }
    )
    assert await runs.compare_and_set(RunStatus.QUEUED, running)

    cancelling = await service.cancel("tenant-a", queued.run_id)

    assert cancelling.status is RunStatus.CANCELLING
    assert await runs.get("tenant-a", queued.run_id) == cancelling
    assert await cancellation_wakeup.wait(
        "tenant-a",
        queued.run_id,
        running.fencing_token,
        timeout_seconds=0.01,
    )
    assert await service.cancel("tenant-a", queued.run_id) == cancelling
    stored_events = await events.list_after("tenant-a", queued.run_id, 0)
    assert [(item.sequence, item.type) for item in stored_events] == [
        (1, "run.queued"),
        (2, "run.cancelling"),
    ]
