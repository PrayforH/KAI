"""Run creation, lookup and cancellation use cases."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast

from harness.application.events import EventService
from harness.application.types import Clock, IdGenerator
from harness.core.errors import ConflictError
from harness.core.models import Run, RunStatus, Session
from harness.core.ports import (
    CancellationWakeup,
    RunRepository,
    RunTask,
    SessionRepository,
    TaskQueue,
)
from harness.core.state_machine import transition
from harness.observability.provider import Observability
from harness.reliability.metrics import ReliabilityMetrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunQuotaPlan:
    max_budget_usd: float | None
    max_model_tokens: int | None
    ttl_seconds: int


@dataclass(frozen=True)
class RunCreation:
    run: Run
    created: bool
    deduplicated: bool


class RunAdmission(Protocol):
    async def admit_run(
        self,
        *,
        tenant_id: str,
        run_id: str,
        user_id: str | None,
        team_ids: tuple[str, ...],
        api_key_id: str | None,
        agent_name: str,
        environment: str | None,
        max_budget_usd: float | None,
        max_model_tokens: int | None,
        ttl_seconds: int,
    ) -> tuple[object, ...]: ...

    async def release_subject(self, tenant_id: str, subject_id: str) -> int: ...


RunQuotaPlanResolver = Callable[[str, str, str, str], Awaitable[RunQuotaPlan]]


def _request_attachment_ids(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        return None
    return tuple(sorted(cast(list[str], items)))


def _same_user_request(left: dict[str, object], right: dict[str, object]) -> bool:
    """Match only the user-owned request identity, not derived routing metadata."""

    left_prompt = left.get("prompt")
    right_prompt = right.get("prompt")
    if not isinstance(left_prompt, str) or not isinstance(right_prompt, str):
        return False
    return left_prompt == right_prompt and _request_attachment_ids(
        left.get("input_artifact_ids", [])
    ) == _request_attachment_ids(right.get("input_artifact_ids", []))


def _blocking_predecessor(active_runs: list[Run]) -> Run | None:
    if not active_runs:
        return None
    ordered = sorted(
        active_runs,
        key=lambda item: (item.created_at, item.run_id),
    )
    return next(
        (item for item in ordered if item.status is RunStatus.WAITING_APPROVAL),
        ordered[0],
    )


def apply_environment_quota(plan: RunQuotaPlan, session: Session) -> RunQuotaPlan:
    # Historical agent/environment budgets cannot re-enable operational limits.
    return RunQuotaPlan(max_budget_usd=None, max_model_tokens=None, ttl_seconds=plan.ttl_seconds)


class RunService:
    def __init__(
        self,
        sessions: SessionRepository,
        runs: RunRepository,
        queue: TaskQueue,
        events: EventService,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        observability: Observability | None = None,
        metrics: ReliabilityMetrics | None = None,
        admission: RunAdmission | None = None,
        quota_plan_resolver: RunQuotaPlanResolver | None = None,
        cancellation_wakeup: CancellationWakeup | None = None,
    ) -> None:
        self._sessions = sessions
        self._runs = runs
        self._queue = queue
        self._events = events
        self._clock = clock
        self._id_generator = id_generator
        self._observability = observability
        self._metrics = metrics
        self._admission = admission
        self._quota_plan_resolver = quota_plan_resolver
        self._cancellation_wakeup = cancellation_wakeup
        self._creation_locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def _notify_cancellation(self, run: Run) -> None:
        if self._cancellation_wakeup is None:
            return
        try:
            await self._cancellation_wakeup.publish(
                run.tenant_id,
                run.run_id,
                run.fencing_token,
            )
        except Exception:
            # The durable status and run.cancelling event have already won.
            # Redis only shortens convergence and must never break cancellation.
            logger.warning(
                "cancellation wakeup failed; durable polling remains active run_id=%s",
                run.run_id,
                exc_info=True,
            )

    async def create(
        self,
        tenant_id: str,
        session_id: str,
        idempotency_key: str,
        *,
        input: dict[str, object] | None = None,
        deduplicate_active_input: bool = True,
    ) -> Run:
        return (
            await self.create_with_result(
                tenant_id,
                session_id,
                idempotency_key,
                input=input,
                deduplicate_active_input=deduplicate_active_input,
            )
        ).run

    async def create_with_result(
        self,
        tenant_id: str,
        session_id: str,
        idempotency_key: str,
        *,
        input: dict[str, object] | None = None,
        deduplicate_active_input: bool = True,
    ) -> RunCreation:
        lock = self._creation_locks.setdefault((tenant_id, session_id), asyncio.Lock())
        async with lock:
            return await self._create_locked(
                tenant_id,
                session_id,
                idempotency_key,
                input=input,
                deduplicate_active_input=deduplicate_active_input,
            )

    async def _create_locked(
        self,
        tenant_id: str,
        session_id: str,
        idempotency_key: str,
        *,
        input: dict[str, object] | None,
        deduplicate_active_input: bool,
    ) -> RunCreation:
        session = await self._sessions.get(tenant_id, session_id)
        existing = await self._runs.find_by_idempotency_key(tenant_id, session_id, idempotency_key)
        if existing is not None:
            self._annotate_trace(
                session_id,
                existing.run_id,
                existing.input.get("prompt"),
            )
            return RunCreation(run=existing, created=False, deduplicated=False)
        run_input = input or {}
        active_runs = [
            item
            for item in await self._runs.list_for_sessions(tenant_id, [session_id], limit=200)
            if not item.status.is_terminal
        ]
        if deduplicate_active_input:
            duplicate = next(
                (item for item in active_runs if _same_user_request(item.input, run_input)),
                None,
            )
            if duplicate is not None:
                self._annotate_trace(
                    session_id,
                    duplicate.run_id,
                    duplicate.input.get("prompt"),
                )
                return RunCreation(run=duplicate, created=False, deduplicated=True)
        timestamp = self._clock()
        run_id = self._id_generator("run")
        self._annotate_trace(session_id, run_id, run_input.get("prompt"))
        run = Run(
            run_id=run_id,
            session_id=session_id,
            tenant_id=tenant_id,
            status=RunStatus.QUEUED,
            idempotency_key=idempotency_key,
            created_at=timestamp,
            updated_at=timestamp,
            input=run_input,
            trace_context=(self._observability.inject() if self._observability is not None else {}),
        )
        admitted = False
        if self._admission is not None:
            plan = (
                await self._quota_plan_resolver(
                    tenant_id,
                    session.resolved_agent_owner_user_id,
                    session.agent_name,
                    session.agent_version,
                )
                if self._quota_plan_resolver is not None
                else RunQuotaPlan(None, None, 3600)
            )
            plan = apply_environment_quota(plan, session)
            await self._admission.admit_run(
                tenant_id=tenant_id,
                run_id=run_id,
                user_id=session.user_id,
                team_ids=session.team_ids,
                api_key_id=session.api_key_id,
                agent_name=session.agent_name,
                environment=session.environment,
                max_budget_usd=plan.max_budget_usd,
                max_model_tokens=plan.max_model_tokens,
                ttl_seconds=plan.ttl_seconds,
            )
            admitted = True
        try:
            await self._runs.add(run)
        except Exception:
            if admitted and self._admission is not None:
                await self._admission.release_subject(tenant_id, run_id)
            raise
        predecessor = _blocking_predecessor(active_runs)
        queue_payload: dict[str, object] = {}
        if predecessor is not None:
            waiting_for_approval = predecessor.status is RunStatus.WAITING_APPROVAL
            queue_payload = {
                "reason_code": (
                    "predecessor_waiting_approval" if waiting_for_approval else "predecessor_active"
                ),
                "reason": ("前序任务等待审批" if waiting_for_approval else "前序任务仍在执行"),
                "blocked_by_run_id": predecessor.run_id,
                "blocked_by_status": predecessor.status.value,
            }
        await self._events.append(
            tenant_id=tenant_id,
            run_id=run.run_id,
            session_id=session_id,
            event_type="run.queued",
            payload=queue_payload,
        )
        await self._queue.enqueue(
            RunTask(tenant_id=tenant_id, run_id=run.run_id, session_id=session_id)
        )
        return RunCreation(run=run, created=True, deduplicated=False)

    def _annotate_trace(
        self,
        session_id: str,
        run_id: str,
        prompt: object | None = None,
    ) -> None:
        if self._observability is None:
            return
        self._observability.annotate_current_span(
            {
                "langfuse.session.id": session_id,
                "langfuse.trace.metadata.run_id": run_id,
                "session.id": session_id,
                "run.id": run_id,
            }
        )
        self._observability.annotate_current_io(
            input_value=prompt,
            trace_level=True,
        )

    async def get(self, tenant_id: str, run_id: str) -> Run:
        return await self._runs.get(tenant_id, run_id)

    async def find_by_idempotency_key(
        self, tenant_id: str, session_id: str, idempotency_key: str
    ) -> Run | None:
        return await self._runs.find_by_idempotency_key(tenant_id, session_id, idempotency_key)

    async def list_for_sessions(
        self, tenant_id: str, session_ids: list[str], *, limit: int = 200
    ) -> list[Run]:
        return await self._runs.list_for_sessions(tenant_id, session_ids, limit=limit)

    async def list_for_tenant(self, tenant_id: str, *, limit: int = 1_000) -> list[Run]:
        return await self._runs.list_for_tenant(tenant_id, limit=limit)

    async def steering_state(self, tenant_id: str, run_id: str) -> dict[str, object]:
        run = await self._runs.get(tenant_id, run_id)
        events = await self._events.list_after(tenant_id, run_id, 0)
        ready = False
        requests: dict[str, dict[str, object]] = {}
        for event in events:
            if event.type == "runtime.steering.ready":
                ready = True
            elif event.type == "runtime.steering.closed":
                ready = False
            elif event.type.startswith("run.steer."):
                key = str(event.payload.get("request_id", ""))
                if event.type == "run.steer.requested" and key in requests:
                    continue
                item = requests.setdefault(key, {"request_id": key})
                item.update(event.payload)
                item["status"] = event.type.rsplit(".", 1)[-1]
        active = ready and run.status is RunStatus.RUNNING
        for item in requests.values():
            if not active and item.get("status") == "requested":
                item["status"] = "not_delivered"
            elif not active and item.get("status") == "sending":
                item["status"] = "unknown"
        return {"available": active, "requests": list(requests.values())}

    async def steer(
        self, tenant_id: str, run_id: str, request_id: str, text: str
    ) -> dict[str, object]:
        state = await self.steering_state(tenant_id, run_id)
        for item in cast(list[dict[str, object]], state["requests"]):
            if item["request_id"] == request_id:
                if item.get("text") != text:
                    raise ConflictError("引导请求编号已用于其他内容")
                return item
        if not state["available"]:
            raise ConflictError("当前运行未就绪或已结束，请将补充加入下一条消息")
        run = await self._runs.get(tenant_id, run_id)
        await self._events.append(
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=run.session_id,
            event_type="run.steer.requested",
            payload={"request_id": request_id, "text": text},
        )
        return {"request_id": request_id, "text": text, "status": "requested"}

    async def cancel(self, tenant_id: str, run_id: str) -> Run:
        current = await self._runs.get(tenant_id, run_id)
        if current.status.is_terminal:
            if self._admission is not None:
                await self._admission.release_subject(tenant_id, run_id)
            return current
        if current.status is RunStatus.CANCELLING:
            await self._notify_cancellation(current)
            return current
        requested_from = current.status
        cancelling = current.model_copy(
            update={
                "status": transition(current.status, RunStatus.CANCELLING),
                "updated_at": self._clock(),
                "fencing_token": current.fencing_token + 1,
            }
        )
        if not await self._runs.compare_and_set(current.status, cancelling):
            current = await self._runs.get(tenant_id, run_id)
            if current.status.is_terminal:
                return current
            if current.status is not RunStatus.CANCELLING:
                raise ConflictError(f"run changed while cancellation was requested: {run_id}")
        else:
            current = cancelling
            await self._events.append(
                tenant_id=tenant_id,
                run_id=run_id,
                session_id=current.session_id,
                event_type="run.cancelling",
            )

        await self._notify_cancellation(current)

        if requested_from in {RunStatus.PROVISIONING, RunStatus.RUNNING}:
            # The Worker owns active external execution. Only it may publish
            # the durable terminal after closing Runtime and child execution.
            return current

        cancelled = current.model_copy(
            update={
                "status": transition(current.status, RunStatus.CANCELLED),
                "updated_at": self._clock(),
                "fencing_token": current.fencing_token + 1,
            }
        )
        if not await self._runs.compare_and_set(RunStatus.CANCELLING, cancelled):
            latest = await self._runs.get(tenant_id, run_id)
            if latest.status.is_terminal:
                return latest
            raise ConflictError(f"run changed while cancellation completed: {run_id}")
        await self._events.append(
            tenant_id=tenant_id,
            run_id=run_id,
            session_id=current.session_id,
            event_type="run.cancelled",
        )
        if self._admission is not None:
            await self._admission.release_subject(tenant_id, run_id)
        if self._metrics is not None:
            self._metrics.observe(
                "harness_workflow_convergence_seconds",
                max(0, (cancelled.updated_at - current.updated_at).total_seconds()),
                labels={"workflow": "run.cancel"},
            )
        return cancelled
