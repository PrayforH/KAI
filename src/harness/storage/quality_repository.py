from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.quality.models import (
    AlertIncident,
    AlertRule,
    DatasetProjection,
    QualityScore,
    QualitySyncJob,
)
from harness.storage.database import SessionFactory
from harness.storage.models import (
    QualityDatasetRow,
    QualityIncidentRow,
    QualityRuleRow,
    QualityScoreRow,
    QualitySyncRow,
)


class PostgresQualityRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def _add(self, row: object, message: str) -> None:
        async with self._sessions() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(message) from error

    async def add_score(self, score: QualityScore) -> None:
        await self._add(
            QualityScoreRow(
                tenant_id=score.tenant_id,
                score_id=score.score_id,
                run_id=score.run_id,
                agent_name=score.agent_name,
                agent_version=score.agent_version,
                name=score.name,
                created_at=score.created_at,
                payload=score.model_dump(mode="json", by_alias=True),
            ),
            "Quality Score already exists",
        )

    async def attach_trace(self, score: QualityScore) -> None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(QualityScoreRow).where(
                    QualityScoreRow.tenant_id == score.tenant_id,
                    QualityScoreRow.score_id == score.score_id,
                ).with_for_update()
            )
            if row is None:
                raise NotFoundError("Quality Score not found")
            previous = QualityScore.model_validate(row.payload)
            if previous.trace_id is None:
                row.payload = previous.model_copy(update={"trace_id": score.trace_id}).model_dump(
                    mode="json", by_alias=True
                )
            await session.commit()

    async def get_score(self, tenant_id: str, score_id: str) -> QualityScore:
        async with self._sessions() as session:
            row = await session.get(QualityScoreRow, (tenant_id, score_id))
            if row is None:
                raise NotFoundError(f"Quality Score not found: {score_id}")
            return QualityScore.model_validate(row.payload)

    async def list_scores(self, tenant_id: str, agent_name: str) -> list[QualityScore]:
        statement = (
            select(QualityScoreRow)
            .where(QualityScoreRow.tenant_id == tenant_id, QualityScoreRow.agent_name == agent_name)
            .order_by(QualityScoreRow.created_at.desc())
        )
        async with self._sessions() as session:
            return [
                QualityScore.model_validate(row.payload)
                for row in (await session.scalars(statement)).all()
            ]

    async def add_rule(self, rule: AlertRule) -> None:
        await self._add(
            QualityRuleRow(
                tenant_id=rule.tenant_id,
                rule_id=rule.rule_id,
                agent_name=rule.agent_name,
                payload=rule.model_dump(mode="json", by_alias=True),
            ),
            "Alert Rule already exists",
        )

    async def list_rules(self, tenant_id: str, agent_name: str) -> list[AlertRule]:
        statement = select(QualityRuleRow).where(
            QualityRuleRow.tenant_id == tenant_id, QualityRuleRow.agent_name == agent_name
        )
        async with self._sessions() as session:
            return [
                AlertRule.model_validate(row.payload)
                for row in (await session.scalars(statement)).all()
            ]

    async def upsert_incident(self, incident: AlertIncident) -> None:
        async with self._sessions() as session:
            row = await session.get(QualityIncidentRow, (incident.tenant_id, incident.incident_id))
            if row is None:
                session.add(
                    QualityIncidentRow(
                        tenant_id=incident.tenant_id,
                        incident_id=incident.incident_id,
                        agent_name=incident.agent_name,
                        state=incident.state.value,
                        payload=incident.model_dump(mode="json", by_alias=True),
                    )
                )
            else:
                row.state, row.payload = (
                    incident.state.value,
                    incident.model_dump(mode="json", by_alias=True),
                )
            await session.commit()

    async def list_incidents(self, tenant_id: str, agent_name: str) -> list[AlertIncident]:
        statement = select(QualityIncidentRow).where(
            QualityIncidentRow.tenant_id == tenant_id, QualityIncidentRow.agent_name == agent_name
        )
        async with self._sessions() as session:
            return [
                AlertIncident.model_validate(row.payload)
                for row in (await session.scalars(statement)).all()
            ]

    async def add_sync(self, job: QualitySyncJob) -> None:
        await self._add(
            QualitySyncRow(
                tenant_id=job.tenant_id,
                sync_id=job.sync_id,
                status=job.status.value,
                payload=job.model_dump(mode="json", by_alias=True),
            ),
            "Quality Sync already exists",
        )

    async def update_sync(self, job: QualitySyncJob) -> None:
        async with self._sessions() as session:
            row = await session.get(QualitySyncRow, (job.tenant_id, job.sync_id))
            if row is None:
                raise NotFoundError(f"Quality Sync not found: {job.sync_id}")
            row.status, row.payload = job.status.value, job.model_dump(mode="json", by_alias=True)
            await session.commit()

    async def get_sync(self, tenant_id: str, sync_id: str) -> QualitySyncJob:
        async with self._sessions() as session:
            row = await session.get(QualitySyncRow, (tenant_id, sync_id))
            if row is None:
                raise NotFoundError(f"Quality Sync not found: {sync_id}")
            return QualitySyncJob.model_validate(row.payload)

    async def add_dataset(self, dataset: DatasetProjection) -> None:
        await self._add(
            QualityDatasetRow(
                tenant_id=dataset.tenant_id,
                projection_id=dataset.projection_id,
                payload=dataset.model_dump(mode="json", by_alias=True),
            ),
            "Dataset Projection already exists",
        )

    async def get_dataset(self, tenant_id: str, projection_id: str) -> DatasetProjection:
        async with self._sessions() as session:
            row = await session.get(QualityDatasetRow, (tenant_id, projection_id))
            if row is None:
                raise NotFoundError(f"Dataset Projection not found: {projection_id}")
            return DatasetProjection.model_validate(row.payload)
