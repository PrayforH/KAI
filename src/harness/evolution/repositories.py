"""An owner-scoped aggregate is replaced atomically with compare-and-swap."""

from __future__ import annotations

import asyncio
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.evolution.models import EvolutionJob


class EvolutionRepository(Protocol):
    async def pending(self) -> list[EvolutionJob]: ...
    async def add(self, job: EvolutionJob) -> None: ...
    async def get(self, tenant: str, owner: str, job_id: str) -> EvolutionJob: ...
    async def list(self, tenant: str, owner: str) -> list[EvolutionJob]: ...
    async def replace(self, expected: int, job: EvolutionJob) -> None: ...


class InMemoryEvolutionRepository:
    def __init__(self) -> None:
        self._jobs: dict[tuple[str, str], EvolutionJob] = {}
        self._lock = asyncio.Lock()

    async def pending(self) -> list[EvolutionJob]:
        return [j for j in self._jobs.values() if j.status == "active"]

    async def add(self, job: EvolutionJob) -> None:
        async with self._lock:
            key = (job.tenant_id, job.job_id)
            if key in self._jobs:
                raise ConflictError("Evolution job already exists")
            self._jobs[key] = job

    async def get(self, tenant: str, owner: str, job_id: str) -> EvolutionJob:
        job = self._jobs.get((tenant, job_id))
        if job is None or job.owner_id != owner:
            raise NotFoundError("Evolution job not found")
        return job

    async def list(self, tenant: str, owner: str) -> list[EvolutionJob]:
        return sorted(
            [j for j in self._jobs.values() if j.tenant_id == tenant and j.owner_id == owner],
            key=lambda j: j.created_at,
            reverse=True,
        )

    async def replace(self, expected: int, job: EvolutionJob) -> None:
        async with self._lock:
            old = await self.get(job.tenant_id, job.owner_id, job.job_id)
            if old.revision != expected or job.revision != expected + 1:
                raise ConflictError("Evolution revision changed; refresh before retrying")
            self._jobs[(job.tenant_id, job.job_id)] = job
