from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.evolution.models import EvolutionJob
from harness.storage.database import SessionFactory
from harness.storage.models import EvolutionJobRow


class PostgresEvolutionRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def pending(self) -> list[EvolutionJob]:
        async with self._sessions() as db:
            rows = await db.scalars(
                select(EvolutionJobRow).where(
                    EvolutionJobRow.payload["status"].as_string() == "active"
                )
            )
            return [self._job(row) for row in rows]

    async def add(self, job: EvolutionJob) -> None:
        async with self._sessions() as db:
            db.add(
                EvolutionJobRow(
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    owner_id=job.owner_id,
                    agent_name=job.agent_name,
                    revision=job.revision,
                    payload=job.model_dump(mode="json", by_alias=True),
                )
            )
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError("Evolution job already exists") from error

    @staticmethod
    def _job(row: EvolutionJobRow) -> EvolutionJob:
        job = EvolutionJob.model_validate(row.payload)
        if (job.tenant_id, job.job_id, job.owner_id, job.revision, job.agent_name) != (
            row.tenant_id,
            row.job_id,
            row.owner_id,
            row.revision,
            row.agent_name,
        ):
            raise ValueError("Corrupt evolution persistence envelope")
        return job

    async def get(self, tenant: str, owner: str, job_id: str) -> EvolutionJob:
        async with self._sessions() as db:
            row = await db.get(EvolutionJobRow, (tenant, job_id))
            if row is None or row.owner_id != owner:
                raise NotFoundError("Evolution job not found")
            return self._job(row)

    async def list(self, tenant: str, owner: str) -> list[EvolutionJob]:
        async with self._sessions() as db:
            rows = await db.scalars(
                select(EvolutionJobRow).where(
                    EvolutionJobRow.tenant_id == tenant,
                    EvolutionJobRow.owner_id == owner,
                )
            )
            return sorted([self._job(r) for r in rows], key=lambda j: j.created_at, reverse=True)

    async def replace(self, expected: int, job: EvolutionJob) -> None:
        if job.revision != expected + 1:
            raise ConflictError("Evolution revision must advance exactly once")
        async with self._sessions() as db:
            result = await db.execute(
                update(EvolutionJobRow)
                .where(
                    EvolutionJobRow.tenant_id == job.tenant_id,
                    EvolutionJobRow.owner_id == job.owner_id,
                    EvolutionJobRow.job_id == job.job_id,
                    EvolutionJobRow.revision == expected,
                )
                .values(revision=job.revision, payload=job.model_dump(mode="json", by_alias=True))
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                await db.rollback()
                raise ConflictError("Evolution revision changed; refresh before retrying")
            await db.commit()
