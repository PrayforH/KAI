from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from datetime import UTC, datetime

from harness.core.errors import ConflictError, NotFoundError
from harness.core.events import RunEvent
from harness.core.models import Artifact, ArtifactStatus, Run, RunStatus, Session
from harness.core.ports import ArtifactRepository, EventRepository, RunRepository, SessionRepository
from harness.evals.models import EvalDatasetVersion
from harness.quality.models import (
    AlertIncident,
    AlertRule,
    AlertState,
    DatasetProjection,
    HumanFeedbackRequest,
    QualityGateResult,
    QualityScore,
    QualitySyncJob,
    QualitySyncStatus,
    ScoreSource,
)
from harness.quality.queue import QualityTask, QualityTaskQueue
from harness.quality.repositories import QualityRepository
from harness.reliability.metrics import ReliabilityMetrics


def _stable(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(':'.join(parts).encode()).hexdigest()[:32]}"


class QualityService:
    def __init__(
        self,
        *,
        repository: QualityRepository,
        queue: QualityTaskQueue,
        runs: RunRepository,
        sessions: SessionRepository,
        events: EventRepository,
        artifacts: ArtifactRepository,
        metrics: ReliabilityMetrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._runs = runs
        self._sessions = sessions
        self._events = events
        self._artifacts = artifacts
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now(UTC))

    async def record_terminal_run(
        self, run: Run, session: Session, trace_id: str
    ) -> list[QualityScore]:
        if not run.status.is_terminal:
            return []
        if self._metrics is not None:
            self._metrics.increment(
                "harness_trace_terminal_total",
                labels={"completeness": "complete" if trace_id else "missing"},
            )
        events = await self._events.list_after(run.tenant_id, run.run_id, 0)
        artifacts = await self._artifacts.list_for_run(run.tenant_id, run.run_id)
        duration = max(0.0, (run.updated_at - run.created_at).total_seconds())
        tool_errors = sum(
            1
            for item in events
            if item.type == "tool.result" and item.payload.get("is_error") is True
        )
        approvals_requested = sum(1 for item in events if item.type == "approval.requested")
        approvals_decided = sum(
            1
            for item in events
            if item.type in {"approval.approved", "approval.rejected", "approval.expired"}
        )
        cost = self._cost(events)
        metrics = {
            "terminal_success": 1.0 if run.status is RunStatus.SUCCEEDED else 0.0,
            "tool_reliability": 1.0 if tool_errors == 0 else 0.0,
            "approval_completion": 1.0 if approvals_decided >= approvals_requested else 0.0,
            "duration_budget": 1.0 if duration <= 900 else 0.0,
            "cost_budget": None if cost is None else (1.0 if cost <= 1 else 0.0),
            "artifact_integrity": 1.0 if self._artifacts_valid(artifacts) else 0.0,
        }
        result: list[QualityScore] = []
        for name, value in metrics.items():
            result.append(
                await self._store_score(
                    run, session, trace_id, name, value, ScoreSource.RULE, "system"
                )
            )
        return result

    async def human_feedback(
        self, *, tenant_id: str, user_id: str, run_id: str, request: HumanFeedbackRequest
    ) -> QualityScore:
        run = await self._runs.get(tenant_id, run_id)
        session = await self._sessions.get(tenant_id, run.session_id)
        if session.user_id != user_id:
            raise NotFoundError(f"Run not found: {run_id}")
        existing = await self._repository.list_scores(tenant_id, session.agent_name)
        trace_id = next((item.trace_id for item in existing if item.run_id == run_id), None)
        return await self._store_score(
            run, session, trace_id, "user_feedback", request.value, ScoreSource.HUMAN, user_id
        )

    async def project_dataset(self, dataset: EvalDatasetVersion) -> DatasetProjection:
        projection = DatasetProjection(
            tenantId=dataset.tenant_id,
            projectionId=_stable("quality_dataset", dataset.dataset_id, str(dataset.version)),
            datasetId=dataset.dataset_id,
            datasetVersion=dataset.version,
            name=dataset.name,
            agentName=dataset.agent_name,
            caseCount=len(dataset.cases),
            contentHash=dataset.source_content_hash,
            createdBy=dataset.created_by,
            createdAt=self._clock(),
        )
        await self._repository.add_dataset(projection)
        await self._enqueue(dataset.tenant_id, "dataset", projection.projection_id)
        return projection

    async def add_rule(self, rule: AlertRule) -> AlertRule:
        await self._repository.add_rule(rule)
        return rule

    async def list_scores(
        self, tenant_id: str, owner_user_id: str, agent_name: str
    ) -> list[QualityScore]:
        result: list[QualityScore] = []
        for score in await self._repository.list_scores(tenant_id, agent_name):
            session = await self._sessions.get(tenant_id, score.session_id)
            if session.resolved_agent_owner_user_id == owner_user_id:
                result.append(score)
        return result

    async def list_rules(
        self, tenant_id: str, owner_user_id: str, agent_name: str
    ) -> list[AlertRule]:
        return [
            item
            for item in await self._repository.list_rules(tenant_id, agent_name)
            if item.created_by == owner_user_id
        ]

    async def list_incidents(
        self, tenant_id: str, owner_user_id: str, agent_name: str
    ) -> list[AlertIncident]:
        return [
            item
            for item in await self._repository.list_incidents(tenant_id, agent_name)
            if item.owner_user_id == owner_user_id
        ]

    async def gate(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> QualityGateResult:
        rules = {
            item.rule_id: item
            for item in await self.list_rules(tenant_id, owner_user_id, agent_name)
        }
        incidents = await self.list_incidents(tenant_id, owner_user_id, agent_name)
        blocking = tuple(
            sorted(
                item.incident_id
                for item in incidents
                if item.agent_version == agent_version
                and item.state is AlertState.OPEN
                and rules.get(item.rule_id)
                and rules[item.rule_id].blocks_promotion
            )
        )
        return QualityGateResult(
            agentName=agent_name,
            agentVersion=agent_version,
            passed=not blocking,
            blockingIncidentIds=blocking,
        )

    async def require_promotion_allowed(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> QualityGateResult:
        gate = await self.gate(tenant_id, owner_user_id, agent_name, agent_version)
        if not gate.passed:
            raise ConflictError(
                "Agent version has blocking quality incidents: "
                + ", ".join(gate.blocking_incident_ids)
            )
        return gate

    async def _store_score(
        self,
        run: Run,
        session: Session,
        trace_id: str | None,
        name: str,
        value: float | None,
        source: ScoreSource,
        created_by: str,
    ) -> QualityScore:
        score = QualityScore(
            tenantId=run.tenant_id,
            scoreId=_stable("quality_score", run.run_id, name, source.value, created_by),
            runId=run.run_id,
            traceId=trace_id or None,
            sessionId=run.session_id,
            agentName=session.agent_name,
            agentVersion=session.agent_version,
            deploymentSnapshotId=session.deployment_snapshot_id,
            evalRunId=(
                str(run.input["eval_run_id"])
                if isinstance(run.input.get("eval_run_id"), str)
                else None
            ),
            name=name,
            value=value,
            source=source,
            createdBy=created_by,
            createdAt=self._clock(),
        )
        try:
            await self._repository.add_score(score)
        except ConflictError:
            existing = await self._repository.get_score(run.tenant_id, score.score_id)
            if existing.value != score.value or existing.source != score.source:
                raise ConflictError(
                    "Quality observation already recorded with another value"
                ) from None
            # Trace enrichment does not change the immutable observation itself.
            if existing.trace_id is None and score.trace_id:
                score = existing.model_copy(update={"trace_id": score.trace_id})
                await self._repository.attach_trace(score)
            else:
                score = existing
        if score.trace_id and score.value is not None:
            await self._enqueue(run.tenant_id, "score", score.score_id)
        await self._evaluate_alerts(score)
        return score

    async def _enqueue(self, tenant_id: str, kind: str, resource_id: str) -> None:
        sync = QualitySyncJob(
            tenantId=tenant_id,
            syncId=_stable("quality_sync", kind, resource_id),
            kind=kind,
            resourceId=resource_id,
            status=QualitySyncStatus.QUEUED,
            createdAt=self._clock(),
            updatedAt=self._clock(),
        )
        try:
            await self._repository.add_sync(sync)
        except ConflictError:
            return
        await self._queue.enqueue(QualityTask(tenant_id=tenant_id, sync_id=sync.sync_id))

    async def _evaluate_alerts(self, score: QualityScore) -> None:
        if score.value is None:
            return
        session = await self._sessions.get(score.tenant_id, score.session_id)
        owner_user_id = session.resolved_agent_owner_user_id
        for rule in await self.list_rules(score.tenant_id, owner_user_id, score.agent_name):
            if not rule.enabled or rule.score_name != score.name:
                continue
            matching = [
                item
                for item in await self.list_scores(score.tenant_id, owner_user_id, score.agent_name)
                if item.agent_version == score.agent_version and item.name == score.name
                and item.value is not None
            ]
            if len(matching) < rule.minimum_samples:
                continue
            total = sum(item.value for item in matching if item.value is not None)
            observed = total / len(matching)
            incident_id = _stable("quality_incident", rule.rule_id, score.agent_version)
            previous = next(
                (
                    item
                    for item in await self.list_incidents(
                        score.tenant_id, owner_user_id, score.agent_name
                    )
                    if item.incident_id == incident_id
                ),
                None,
            )
            state = AlertState.OPEN if observed < rule.minimum_value else AlertState.RESOLVED
            incident = AlertIncident(
                tenantId=score.tenant_id,
                incidentId=incident_id,
                ruleId=rule.rule_id,
                agentName=score.agent_name,
                agentVersion=score.agent_version,
                ownerUserId=owner_user_id,
                state=state,
                observedValue=observed,
                sampleCount=len(matching),
                openedAt=previous.opened_at if previous else self._clock(),
                resolvedAt=self._clock() if state is AlertState.RESOLVED else None,
            )
            await self._repository.upsert_incident(incident)

    @staticmethod
    def _cost(events: list[RunEvent]) -> float | None:
        for event in reversed(events):
            if event.type == "runtime.result":
                value = event.payload.get("total_cost_usd")
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) and value >= 0):
                    return float(value)
        return None

    @staticmethod
    def _artifacts_valid(artifacts: list[Artifact]) -> bool:
        return all(item.status is ArtifactStatus.READY and bool(item.sha256) for item in artifacts)
