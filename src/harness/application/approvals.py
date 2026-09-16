"""Approval request and decision use cases."""

import asyncio
from datetime import timedelta
from typing import Any

from harness.application.events import EventService
from harness.application.types import Clock, IdGenerator
from harness.core.errors import ConflictError
from harness.core.models import ApprovalRequest, ApprovalStatus, Run, RunStatus
from harness.core.ports import (
    ApprovalRepository,
    RunRepository,
    RunTask,
    TaskQueue,
)
from harness.core.state_machine import transition
from harness.observability.provider import Observability
from harness.reliability.metrics import ReliabilityMetrics


class ApprovalService:
    def __init__(
        self,
        *,
        runs: RunRepository,
        approvals: ApprovalRepository,
        events: EventService,
        clock: Clock,
        id_generator: IdGenerator,
        queue: TaskQueue | None = None,
        ttl: timedelta = timedelta(minutes=15),
        decision_poll_interval_seconds: float = 0.25,
        observability: Observability | None = None,
        metrics: ReliabilityMetrics | None = None,
    ) -> None:
        if decision_poll_interval_seconds <= 0:
            raise ValueError("approval decision poll interval must be positive")
        self._runs = runs
        self._approvals = approvals
        self._events = events
        self._clock = clock
        self._id_generator = id_generator
        self._queue = queue
        self._ttl = ttl
        self._decision_poll_interval = decision_poll_interval_seconds
        self._observability = observability
        self._metrics = metrics
        self._inline_waiters: dict[str, asyncio.Future[ApprovalStatus]] = {}
        self._inline_tenants: dict[str, str] = {}

    def _record_span(
        self,
        run: Run,
        approval: ApprovalRequest,
        *,
        name: str,
        status: str,
    ) -> None:
        if self._observability is None:
            return
        with self._observability.span(
            name,
            carrier=run.trace_context,
            attributes={
                "run.id": run.run_id,
                "harness.approval.id": approval.approval_id,
                "harness.approval.tool_call_id": approval.tool_call_id,
                "harness.approval.tool_name": approval.tool_name or "unknown",
                "harness.approval.status": status,
                "harness.approval.risk": approval.risk or "unknown",
                "harness.policy.rule": approval.policy_rule or "unknown",
                "langfuse.observation.type": "guardrail",
                "langfuse.observation.level": ("WARNING" if status == "pending" else "DEFAULT"),
                "langfuse.observation.status_message": status,
                "langfuse.observation.metadata.tool_name": (approval.tool_name or "unknown"),
                "langfuse.observation.metadata.risk": approval.risk or "unknown",
            },
        ):
            self._observability.annotate_current_io(
                input_value={
                    "reason": approval.reason,
                    "arguments": approval.argument_summary,
                },
                output_value={"status": status},
            )

    def has_inline_waiter(self, approval_id: str) -> bool:
        return approval_id in self._inline_waiters

    async def get(self, tenant_id: str, approval_id: str) -> ApprovalRequest:
        return await self._approvals.get(tenant_id, approval_id)

    async def list_for_runs(self, tenant_id: str, run_ids: list[str]) -> list[ApprovalRequest]:
        return await self._approvals.list_for_runs(tenant_id, run_ids)

    def _register_inline_waiter(self, tenant_id: str, approval_id: str) -> None:
        if approval_id not in self._inline_waiters:
            self._inline_waiters[approval_id] = asyncio.get_running_loop().create_future()
            self._inline_tenants[approval_id] = tenant_id

    async def wait_for_decision(self, approval_id: str) -> ApprovalStatus:
        future = self._inline_waiters.get(approval_id)
        tenant_id = self._inline_tenants.get(approval_id)
        if future is None or tenant_id is None:
            raise ConflictError(f"inline approval waiter is not registered: {approval_id}")
        try:
            while True:
                current = await self._approvals.get(tenant_id, approval_id)
                if current.status is not ApprovalStatus.PENDING:
                    if current.inline and current.status is ApprovalStatus.APPROVED:
                        await self._ensure_inline_run_resumed(current)
                    return current.status
                run = await self._runs.get(tenant_id, current.run_id)
                if run.status is RunStatus.CANCELLING or run.status.is_terminal:
                    cancelled = await self._cancel_pending(current, run)
                    return cancelled.status
                if self._clock() >= current.expires_at:
                    expired = await self._expire(current)
                    return expired.status
                completed, _ = await asyncio.wait({future}, timeout=self._decision_poll_interval)
                if completed:
                    return future.result()
        finally:
            self._inline_waiters.pop(approval_id, None)
            self._inline_tenants.pop(approval_id, None)

    async def _cancel_pending(
        self,
        current: ApprovalRequest,
        run: Run,
        *,
        reason: str = "run cancellation requested",
    ) -> ApprovalRequest:
        cancelled = current.model_copy(update={"status": ApprovalStatus.CANCELLED})
        changed = await self._approvals.compare_and_set(ApprovalStatus.PENDING, cancelled)
        if not changed:
            return await self._approvals.get(current.tenant_id, current.approval_id)
        await self._events.append(
            tenant_id=current.tenant_id,
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="approval.cancelled",
            payload={
                "approval_id": current.approval_id,
                "reason": reason,
            },
        )
        waiter = self._inline_waiters.get(current.approval_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(ApprovalStatus.CANCELLED)
        return cancelled

    async def cancel_pending(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        reason: str,
    ) -> ApprovalRequest:
        """Close a pending approval when its owning SDK wait is interrupted."""
        current = await self._approvals.get(tenant_id, approval_id)
        if current.status is not ApprovalStatus.PENDING:
            return current
        run = await self._runs.get(tenant_id, current.run_id)
        return await self._cancel_pending(current, run, reason=reason)

    async def _ensure_inline_run_resumed(self, approval: ApprovalRequest) -> None:
        run = await self._runs.get(approval.tenant_id, approval.run_id)
        if run.status is not RunStatus.WAITING_APPROVAL:
            return
        try:
            await self._move(run, RunStatus.RUNNING)
        except ConflictError:
            # Another process may have completed the same durable transition.
            refreshed = await self._runs.get(approval.tenant_id, approval.run_id)
            if refreshed.status is RunStatus.WAITING_APPROVAL:
                raise

    async def _enqueue_resumed_run(self, approval: ApprovalRequest) -> None:
        if self._queue is None:
            return
        run = await self._runs.get(approval.tenant_id, approval.run_id)
        await self._queue.enqueue(
            RunTask(
                tenant_id=approval.tenant_id,
                run_id=approval.run_id,
                session_id=run.session_id,
            )
        )

    async def _expire(self, current: ApprovalRequest) -> ApprovalRequest:
        expired = current.model_copy(update={"status": ApprovalStatus.EXPIRED})
        changed = await self._approvals.compare_and_set(ApprovalStatus.PENDING, expired)
        if not changed:
            return await self._approvals.get(current.tenant_id, current.approval_id)
        run = await self._runs.get(current.tenant_id, current.run_id)
        await self._events.append(
            tenant_id=current.tenant_id,
            run_id=run.run_id,
            session_id=run.session_id,
            event_type="approval.expired",
            payload={"approval_id": current.approval_id},
        )
        # Expiry has the same durable outcome on API and Worker processes.
        # A local in-memory waiter must not determine whether the Run resumes.
        if not run.status.is_terminal and run.status is not RunStatus.CANCELLING:
            await self._events.append(
                tenant_id=current.tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                event_type="tool.result",
                payload={
                    "tool_call_id": current.tool_call_id,
                    "is_error": True,
                    "error": {
                        "code": "approval_expired",
                        "message": "tool approval expired",
                    },
                },
            )
            if run.status is RunStatus.WAITING_APPROVAL:
                await self._move(run, RunStatus.REJECTED)
        waiter = self._inline_waiters.get(current.approval_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(ApprovalStatus.EXPIRED)
        return expired

    async def reap_expired(self, *, limit: int = 100) -> int:
        """Expire orphaned approvals even when no SDK waiter is alive."""
        if limit < 1:
            raise ValueError("approval reaper limit must be positive")
        due = await self._approvals.list_expired_pending(self._clock(), limit=limit)
        expired_count = 0
        for approval in due:
            current = await self._approvals.get(approval.tenant_id, approval.approval_id)
            if current.status is not ApprovalStatus.PENDING or self._clock() < current.expires_at:
                continue
            result = await self._expire(current)
            if result.status is ApprovalStatus.EXPIRED:
                expired_count += 1
        return expired_count

    async def _move(self, run: Run, status: RunStatus) -> Run:
        updated = run.model_copy(
            update={
                "status": transition(run.status, status),
                "updated_at": self._clock(),
                "fencing_token": run.fencing_token + 1,
            }
        )
        if not await self._runs.compare_and_set(run.status, updated):
            raise ConflictError(f"run changed during approval: {run.run_id}")
        await self._events.append(
            tenant_id=run.tenant_id,
            run_id=run.run_id,
            session_id=run.session_id,
            event_type=f"run.{status.value}",
        )
        return updated

    async def request(
        self,
        *,
        tenant_id: str,
        run_id: str,
        tool_call_id: str,
        reason: str,
        message_id: str | None = None,
        inline: bool = False,
        tool_name: str | None = None,
        argument_summary: dict[str, Any] | None = None,
        sandbox_provider: str | None = None,
        sandbox_isolation: str | None = None,
        policy_rule: str | None = None,
        risk: str | None = None,
    ) -> ApprovalRequest:
        existing = await self._approvals.find_by_tool_call(tenant_id, run_id, tool_call_id)
        if existing is not None:
            if inline and existing.status is ApprovalStatus.PENDING:
                self._register_inline_waiter(tenant_id, existing.approval_id)
            return existing
        run = await self._runs.get(tenant_id, run_id)
        if run.status is not RunStatus.RUNNING:
            raise ConflictError("approval can only pause a running Run")
        now = self._clock()
        approval = ApprovalRequest(
            approval_id=self._id_generator("approval"),
            run_id=run_id,
            tenant_id=tenant_id,
            tool_call_id=tool_call_id,
            status=ApprovalStatus.PENDING,
            reason=reason,
            expires_at=now + self._ttl,
            created_at=now,
            inline=inline,
            tool_name=tool_name,
            argument_summary=argument_summary or {},
            sandbox_provider=sandbox_provider,
            sandbox_isolation=sandbox_isolation,
            policy_rule=policy_rule,
            risk=risk,
        )
        if inline:
            self._register_inline_waiter(tenant_id, approval.approval_id)
        await self._approvals.add(approval)
        payload = approval.model_dump(mode="json")
        if message_id is not None:
            payload["message_id"] = message_id
        await self._events.append(
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=run.session_id,
            event_type="approval.requested",
            payload=payload,
        )
        await self._move(run, RunStatus.WAITING_APPROVAL)
        self._record_span(
            run,
            approval,
            name="harness.approval.request",
            status="pending",
        )
        return approval

    async def decide(
        self,
        *,
        tenant_id: str,
        approval_id: str,
        decision: ApprovalStatus,
    ) -> ApprovalRequest:
        if decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ConflictError("approval decision must be approved or rejected")
        current = await self._approvals.get(tenant_id, approval_id)
        if current.status is decision:
            if current.inline and decision is ApprovalStatus.APPROVED:
                await self._ensure_inline_run_resumed(current)
            if decision is ApprovalStatus.APPROVED and not current.inline:
                await self._enqueue_resumed_run(current)
            return current
        if current.status is not ApprovalStatus.PENDING:
            raise ConflictError(f"approval is already {current.status.value}")
        run = await self._runs.get(tenant_id, current.run_id)
        if run.status.is_terminal or run.status is RunStatus.CANCELLING:
            await self._cancel_pending(current, run, reason="run is no longer active")
            raise ConflictError("运行已结束或正在停止，无法继续审批")
        decided_at = self._clock()
        if decided_at >= current.expires_at:
            await self._expire(current)
            raise ConflictError("approval has expired")
        updated = current.model_copy(update={"status": decision})
        if not await self._approvals.compare_and_set(ApprovalStatus.PENDING, updated):
            raise ConflictError("approval changed while decision was applied")
        if self._metrics is not None:
            self._metrics.observe(
                "harness_workflow_convergence_seconds",
                max(0, (decided_at - current.created_at).total_seconds()),
                labels={"workflow": "approval.decide"},
            )
        run = await self._runs.get(tenant_id, current.run_id)
        await self._events.append(
            tenant_id=tenant_id,
            run_id=run.run_id,
            session_id=run.session_id,
            event_type=f"approval.{decision.value}",
            payload={"approval_id": approval_id},
        )
        self._record_span(
            run,
            updated,
            name="harness.approval.decision",
            status=decision.value,
        )
        inline = current.inline
        if decision is ApprovalStatus.APPROVED:
            await self._move(run, RunStatus.RUNNING)
            # An inline SDK hook keeps the original worker task and polls the
            # durable decision. Enqueuing a second copy here races that live
            # executor and can leave the Run fenced in RUNNING after outputs
            # were already archived. Non-inline approvals still need a fresh
            # worker handoff.
            if decision is ApprovalStatus.APPROVED and not inline:
                await self._enqueue_resumed_run(updated)
        else:
            if not inline:
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run.run_id,
                    session_id=run.session_id,
                    event_type="tool.result",
                    payload={
                        "tool_call_id": current.tool_call_id,
                        "is_error": True,
                        "error": {
                            "code": "approval_rejected",
                            "message": "tool use rejected",
                        },
                    },
                )
            await self._move(run, RunStatus.REJECTED)
        waiter = self._inline_waiters.get(approval_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(decision)
        return updated
