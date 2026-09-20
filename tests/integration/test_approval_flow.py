import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import (
    InMemoryApprovalRepository,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
    InMemoryTaskQueue,
)
from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.core.errors import ConflictError
from harness.core.models import ApprovalStatus, Run, RunStatus
from harness.core.ports import RunTask
from harness.reliability.metrics import ReliabilityMetrics

NOW = datetime(2026, 7, 11, tzinfo=UTC)


def ids() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return generate


async def arrange(
    *,
    clock: Callable[[], datetime] = lambda: NOW,
    queue: InMemoryTaskQueue | None = None,
    metrics: ReliabilityMetrics | None = None,
):
    runs = InMemoryRunRepository()
    approvals = InMemoryApprovalRepository()
    events = InMemoryEventRepository()
    run = Run(
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="idem-1",
        created_at=NOW,
        updated_at=NOW,
    )
    await runs.add(run)
    service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=EventService(events, InMemoryEventBus(), clock=clock, id_generator=ids()),
        clock=clock,
        id_generator=ids(),
        queue=queue,
        ttl=timedelta(minutes=5),
        metrics=metrics,
    )
    return service, runs, events


@pytest.mark.asyncio
async def test_approval_request_is_idempotent_and_approval_resumes_run() -> None:
    service, runs, events = await arrange()

    first = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="Write requires review",
    )
    repeated = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="Write requires review",
    )
    approved = await service.decide(
        tenant_id="tenant-a",
        approval_id=first.approval_id,
        decision=ApprovalStatus.APPROVED,
    )

    assert repeated.approval_id == first.approval_id
    assert approved.status is ApprovalStatus.APPROVED
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.RUNNING
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert [event.type for event in emitted] == [
        "approval.requested",
        "run.waiting_approval",
        "approval.approved",
        "run.running",
    ]


@pytest.mark.asyncio
async def test_approval_decision_records_durable_wait_time() -> None:
    current = NOW

    def clock() -> datetime:
        return current

    metrics = ReliabilityMetrics()
    service, _, _ = await arrange(clock=clock, metrics=metrics)
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-timed",
        reason="review",
    )
    current = NOW + timedelta(seconds=7)

    await service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.APPROVED,
    )
    await service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.APPROVED,
    )

    assert metrics.quantile(
        "harness_workflow_convergence_seconds",
        0.95,
        labels={"workflow": "approval.decide"},
    ) == (7, 1)


@pytest.mark.asyncio
async def test_rejection_emits_structured_tool_error_and_stops_run() -> None:
    service, runs, events = await arrange()
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="review",
    )

    rejected = await service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.REJECTED,
    )

    assert rejected.status is ApprovalStatus.REJECTED
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.REJECTED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-2].type == "tool.result"
    assert emitted[-2].payload == {
        "tool_call_id": "tool-1",
        "is_error": True,
        "error": {"code": "approval_rejected", "message": "tool use rejected"},
    }


@pytest.mark.asyncio
async def test_expired_approval_cannot_execute() -> None:
    current = NOW

    def clock() -> datetime:
        return current

    service, _, _ = await arrange(clock=clock)
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-1",
        reason="review",
    )
    current = NOW + timedelta(minutes=6)

    with pytest.raises(ConflictError, match="expired"):
        await service.decide(
            tenant_id="tenant-a",
            approval_id=approval.approval_id,
            decision=ApprovalStatus.APPROVED,
        )


@pytest.mark.asyncio
async def test_orphaned_expired_approval_is_reaped_and_run_is_rejected() -> None:
    current = NOW

    def clock() -> datetime:
        return current

    runs = InMemoryRunRepository()
    approvals = InMemoryApprovalRepository()
    events = InMemoryEventRepository()
    event_service = EventService(events, InMemoryEventBus(), clock=clock, id_generator=ids())
    await runs.add(
        Run(
            run_id="run-orphan",
            session_id="session-1",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="orphan",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    worker_before_restart = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=clock,
        id_generator=ids(),
        ttl=timedelta(minutes=5),
    )
    approval = await worker_before_restart.request(
        tenant_id="tenant-a",
        run_id="run-orphan",
        tool_call_id="tool-orphan",
        reason="review",
        inline=True,
    )
    assert worker_before_restart.has_inline_waiter(approval.approval_id)

    current = NOW + timedelta(minutes=6)
    worker_after_restart = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=clock,
        id_generator=ids(),
        ttl=timedelta(minutes=5),
    )

    assert await worker_after_restart.reap_expired() == 1
    assert (await approvals.get("tenant-a", approval.approval_id)).status is ApprovalStatus.EXPIRED
    assert (await runs.get("tenant-a", "run-orphan")).status is RunStatus.REJECTED
    emitted = await events.list_after("tenant-a", "run-orphan", 0)
    assert [event.type for event in emitted][-3:] == [
        "approval.expired",
        "tool.result",
        "run.rejected",
    ]


@pytest.mark.asyncio
async def test_approved_handoff_enqueues_a_fresh_worker_task() -> None:
    queue = InMemoryTaskQueue()
    service, _, _ = await arrange(queue=queue)
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-handoff",
        reason="review",
    )

    await service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.APPROVED,
    )

    assert await queue.dequeue() == RunTask(
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
    )


@pytest.mark.asyncio
async def test_inline_approval_wakes_the_waiting_sdk_call_and_cleans_up() -> None:
    queue = InMemoryTaskQueue()
    service, runs, events = await arrange(queue=queue)

    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-inline",
        reason="Bash requires review",
        inline=True,
    )

    assert service.has_inline_waiter(approval.approval_id)
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[0].type == "approval.requested"
    waiting = asyncio.create_task(service.wait_for_decision(approval.approval_id))
    await service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.APPROVED,
    )

    assert await waiting is ApprovalStatus.APPROVED
    assert not service.has_inline_waiter(approval.approval_id)
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.RUNNING
    assert await queue.dequeue() is None


@pytest.mark.asyncio
async def test_inline_rejection_terminates_run_without_resuming_sdk() -> None:
    service, runs, events = await arrange()
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-inline",
        reason="review",
        inline=True,
    )
    waiting = asyncio.create_task(service.wait_for_decision(approval.approval_id))

    await service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.REJECTED,
    )

    assert await waiting is ApprovalStatus.REJECTED
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.REJECTED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    event_types = [event.type for event in emitted]
    assert event_types == [
        "approval.requested",
        "run.waiting_approval",
        "approval.rejected",
        "run.rejected",
    ]
    assert "tool.result" not in event_types


@pytest.mark.asyncio
async def test_inline_decision_crosses_api_and_worker_service_instances() -> None:
    runs = InMemoryRunRepository()
    approvals = InMemoryApprovalRepository()
    events = InMemoryEventRepository()
    event_service = EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids())
    await runs.add(
        Run(
            run_id="run-cross-process",
            session_id="session-1",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="cross-process",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    worker_service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=lambda: NOW,
        id_generator=ids(),
        decision_poll_interval_seconds=0.001,
    )
    api_service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=lambda: NOW,
        id_generator=ids(),
        decision_poll_interval_seconds=0.001,
    )
    approval = await worker_service.request(
        tenant_id="tenant-a",
        run_id="run-cross-process",
        tool_call_id="tool-cross-process",
        reason="Bash requires review",
        inline=True,
    )
    waiting = asyncio.create_task(worker_service.wait_for_decision(approval.approval_id))

    assert not api_service.has_inline_waiter(approval.approval_id)
    await api_service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.REJECTED,
    )

    assert await asyncio.wait_for(waiting, timeout=0.2) is ApprovalStatus.REJECTED
    assert (await runs.get("tenant-a", "run-cross-process")).status is RunStatus.REJECTED


@pytest.mark.asyncio
async def test_cross_process_inline_approval_does_not_duplicate_live_worker_task() -> None:
    runs = InMemoryRunRepository()
    approvals = InMemoryApprovalRepository()
    events = InMemoryEventRepository()
    queue = InMemoryTaskQueue()
    event_service = EventService(
        events,
        InMemoryEventBus(),
        clock=lambda: NOW,
        id_generator=ids(),
    )
    await runs.add(
        Run(
            run_id="run-cross-process-approved",
            session_id="session-1",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="cross-process-approved",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    worker_service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=lambda: NOW,
        id_generator=ids(),
        queue=queue,
        decision_poll_interval_seconds=0.001,
    )
    api_service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=lambda: NOW,
        id_generator=ids(),
        queue=queue,
        decision_poll_interval_seconds=0.001,
    )
    approval = await worker_service.request(
        tenant_id="tenant-a",
        run_id="run-cross-process-approved",
        tool_call_id="tool-cross-process-approved",
        reason="Bash requires review",
        inline=True,
    )
    waiting = asyncio.create_task(
        worker_service.wait_for_decision(approval.approval_id)
    )

    await api_service.decide(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        decision=ApprovalStatus.APPROVED,
    )

    assert await asyncio.wait_for(waiting, timeout=0.2) is ApprovalStatus.APPROVED
    assert await queue.dequeue() is None


@pytest.mark.asyncio
async def test_run_cancellation_releases_inline_approval_waiter() -> None:
    service, runs, events = await arrange()
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-cancelled",
        reason="review",
        inline=True,
    )
    waiting = asyncio.create_task(service.wait_for_decision(approval.approval_id))
    current = await runs.get("tenant-a", "run-1")
    cancelling = current.model_copy(
        update={
            "status": RunStatus.CANCELLING,
            "fencing_token": current.fencing_token + 1,
        }
    )
    assert await runs.compare_and_set(RunStatus.WAITING_APPROVAL, cancelling)

    assert await asyncio.wait_for(waiting, timeout=0.5) is ApprovalStatus.CANCELLED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-1].type == "approval.cancelled"


@pytest.mark.asyncio
async def test_interrupted_inline_wait_can_be_closed_idempotently() -> None:
    service, _, events = await arrange()
    approval = await service.request(
        tenant_id="tenant-a",
        run_id="run-1",
        tool_call_id="tool-interrupted",
        reason="review",
        inline=True,
    )

    cancelled = await service.cancel_pending(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        reason="runtime timeout",
    )
    repeated = await service.cancel_pending(
        tenant_id="tenant-a",
        approval_id=approval.approval_id,
        reason="ignored duplicate",
    )

    assert cancelled.status is ApprovalStatus.CANCELLED
    assert repeated == cancelled
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert [event.type for event in emitted].count("approval.cancelled") == 1
    assert emitted[-1].payload["reason"] == "runtime timeout"


@pytest.mark.asyncio
async def test_inline_expiry_is_terminal_even_when_waiter_is_local() -> None:
    now = NOW
    service, runs, _ = await arrange(clock=lambda: now)
    approval = await service.request(
        tenant_id="tenant-a", run_id="run-1", tool_call_id="local-expiry",
        reason="confirm", inline=True,
    )
    now += timedelta(minutes=6)
    assert await service.wait_for_decision(approval.approval_id) is ApprovalStatus.EXPIRED
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.REJECTED


@pytest.mark.asyncio
async def test_decision_after_cancellation_does_not_approve_or_restart_run() -> None:
    service, runs, events = await arrange()
    approval = await service.request(tenant_id="tenant-a", run_id="run-1", tool_call_id="cancelled",
                                     reason="confirm", inline=True)
    current = await runs.get("tenant-a", "run-1")
    assert await runs.compare_and_set(current.status, current.model_copy(update={
        "status": RunStatus.CANCELLED, "fencing_token": current.fencing_token + 1,
    }))
    with pytest.raises(ConflictError, match="运行已结束"):
        await service.decide(tenant_id="tenant-a", approval_id=approval.approval_id,
                             decision=ApprovalStatus.APPROVED)
    assert (await service.get("tenant-a", approval.approval_id)).status is ApprovalStatus.CANCELLED
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.CANCELLED
    assert not any(
        e.type == "approval.approved" for e in await events.list_after("tenant-a", "run-1", 0)
    )
    assert await service.wait_for_decision(approval.approval_id) is ApprovalStatus.CANCELLED
