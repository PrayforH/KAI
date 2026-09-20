from __future__ import annotations

import asyncio
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.quality.models import (
    AlertIncident,
    AlertRule,
    DatasetProjection,
    QualityScore,
    QualitySyncJob,
)


class QualityRepository(Protocol):
    async def add_score(self, score: QualityScore) -> None: ...
    async def attach_trace(self, score: QualityScore) -> None: ...
    async def get_score(self, tenant_id: str, score_id: str) -> QualityScore: ...
    async def list_scores(self, tenant_id: str, agent_name: str) -> list[QualityScore]: ...
    async def add_rule(self, rule: AlertRule) -> None: ...
    async def list_rules(self, tenant_id: str, agent_name: str) -> list[AlertRule]: ...
    async def upsert_incident(self, incident: AlertIncident) -> None: ...
    async def list_incidents(self, tenant_id: str, agent_name: str) -> list[AlertIncident]: ...
    async def add_sync(self, job: QualitySyncJob) -> None: ...
    async def update_sync(self, job: QualitySyncJob) -> None: ...
    async def get_sync(self, tenant_id: str, sync_id: str) -> QualitySyncJob: ...
    async def add_dataset(self, dataset: DatasetProjection) -> None: ...
    async def get_dataset(self, tenant_id: str, projection_id: str) -> DatasetProjection: ...


class InMemoryQualityRepository:
    def __init__(self) -> None:
        self._scores: dict[tuple[str, str], QualityScore] = {}
        self._rules: dict[tuple[str, str], AlertRule] = {}
        self._incidents: dict[tuple[str, str], AlertIncident] = {}
        self._syncs: dict[tuple[str, str], QualitySyncJob] = {}
        self._datasets: dict[tuple[str, str], DatasetProjection] = {}
        self._lock = asyncio.Lock()

    async def add_score(self, score: QualityScore) -> None:
        async with self._lock:
            key = (score.tenant_id, score.score_id)
            existing = self._scores.get(key)
            if existing is not None and existing != score:
                raise ConflictError("Quality Score already exists")
            self._scores[key] = score

    async def attach_trace(self, score: QualityScore) -> None:
        async with self._lock:
            previous = self._scores[(score.tenant_id, score.score_id)]
            if previous.trace_id is None:
                self._scores[(score.tenant_id, score.score_id)] = previous.model_copy(
                    update={"trace_id": score.trace_id}
                )

    async def get_score(self, tenant_id: str, score_id: str) -> QualityScore:
        try:
            return self._scores[(tenant_id, score_id)]
        except KeyError as error:
            raise NotFoundError(f"Quality Score not found: {score_id}") from error

    async def list_scores(self, tenant_id: str, agent_name: str) -> list[QualityScore]:
        return sorted(
            (
                item
                for (tenant, _), item in self._scores.items()
                if tenant == tenant_id and item.agent_name == agent_name
            ),
            key=lambda item: (item.created_at, item.score_id),
            reverse=True,
        )

    async def add_rule(self, rule: AlertRule) -> None:
        async with self._lock:
            key = (rule.tenant_id, rule.rule_id)
            if key in self._rules:
                raise ConflictError("Alert Rule already exists")
            self._rules[key] = rule

    async def list_rules(self, tenant_id: str, agent_name: str) -> list[AlertRule]:
        return [
            item
            for (tenant, _), item in self._rules.items()
            if tenant == tenant_id and item.agent_name == agent_name
        ]

    async def upsert_incident(self, incident: AlertIncident) -> None:
        self._incidents[(incident.tenant_id, incident.incident_id)] = incident

    async def list_incidents(self, tenant_id: str, agent_name: str) -> list[AlertIncident]:
        return [
            item
            for (tenant, _), item in self._incidents.items()
            if tenant == tenant_id and item.agent_name == agent_name
        ]

    async def add_sync(self, job: QualitySyncJob) -> None:
        async with self._lock:
            key = (job.tenant_id, job.sync_id)
            if key in self._syncs:
                raise ConflictError("Quality Sync already exists")
            self._syncs[key] = job

    async def update_sync(self, job: QualitySyncJob) -> None:
        self._syncs[(job.tenant_id, job.sync_id)] = job

    async def get_sync(self, tenant_id: str, sync_id: str) -> QualitySyncJob:
        try:
            return self._syncs[(tenant_id, sync_id)]
        except KeyError as error:
            raise NotFoundError(f"Quality Sync not found: {sync_id}") from error

    async def add_dataset(self, dataset: DatasetProjection) -> None:
        self._datasets[(dataset.tenant_id, dataset.projection_id)] = dataset

    async def get_dataset(self, tenant_id: str, projection_id: str) -> DatasetProjection:
        try:
            return self._datasets[(tenant_id, projection_id)]
        except KeyError as error:
            raise NotFoundError(f"Dataset Projection not found: {projection_id}") from error
