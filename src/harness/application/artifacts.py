"""Artifact metadata and object lifecycle use cases."""

from harness.application.types import IdGenerator
from harness.core.errors import ConflictError
from harness.core.models import Artifact, ArtifactStatus
from harness.core.ports import (
    ArtifactRepository,
    ArtifactStore,
    RunRepository,
    SessionRepository,
)
from harness.deployments.boundaries import environment_quota_boundary
from harness.quota.models import QuotaResource, ResourceReservation
from harness.quota.service import QuotaService


class ArtifactService:
    def __init__(
        self,
        *,
        runs: RunRepository,
        repository: ArtifactRepository,
        store: ArtifactStore,
        id_generator: IdGenerator,
        max_file_bytes: int = 50 * 1024 * 1024,
        sessions: SessionRepository | None = None,
        quotas: QuotaService | None = None,
    ) -> None:
        self._runs = runs
        self._repository = repository
        self._store = store
        self._id_generator = id_generator
        self.max_file_bytes = max_file_bytes
        self._sessions = sessions
        self._quotas = quotas

    async def upload(
        self,
        *,
        tenant_id: str,
        run_id: str,
        name: str,
        media_type: str,
        content: bytes,
    ) -> Artifact:
        if len(content) > self.max_file_bytes:
            raise ConflictError(f"artifact exceeds maximum size of {self.max_file_bytes} bytes")
        run = await self._runs.get(tenant_id, run_id)
        session = (
            await self._sessions.get(tenant_id, run.session_id)
            if self._sessions is not None
            else None
        )
        boundary = environment_quota_boundary(session) if session is not None else None
        if (
            boundary is not None
            and boundary.max_artifact_bytes is not None
            and len(content) > boundary.max_artifact_bytes
        ):
            raise ConflictError(
                f"artifact exceeds Environment maximum size of {boundary.max_artifact_bytes} bytes"
            )
        artifact_id = self._id_generator("artifact")
        reservation: ResourceReservation | None = None
        if self._quotas is not None:
            reservation = await self._quotas.reserve(
                tenant_id=tenant_id,
                resource=QuotaResource.ARTIFACT_BYTES,
                amount=max(1, len(content)),
                subject_id=artifact_id,
                idempotency_key=f"artifact:{artifact_id}:bytes",
                agent_name=session.agent_name if session is not None else None,
                environment=session.environment if session is not None else None,
            )
        pending = Artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            tenant_id=tenant_id,
            name=name,
            media_type=media_type,
            status=ArtifactStatus.PENDING,
            object_key=f".pending/{tenant_id}/{artifact_id}",
        )
        try:
            await self._repository.add(pending)
        except Exception:
            if reservation is not None and self._quotas is not None:
                await self._quotas.release(reservation)
            raise
        try:
            stored = await self._store.put(tenant_id, artifact_id, content)
        except Exception:
            await self._repository.update(
                pending.model_copy(update={"status": ArtifactStatus.FAILED})
            )
            if reservation is not None and self._quotas is not None:
                await self._quotas.release(reservation)
            raise
        ready = pending.model_copy(
            update={
                "status": ArtifactStatus.READY,
                "object_key": stored.object_key,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
            }
        )
        await self._repository.update(ready)
        if reservation is not None and self._quotas is not None:
            await self._quotas.commit(reservation, amount=len(content))
        return ready

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[Artifact]:
        await self._runs.get(tenant_id, run_id)
        return await self._repository.list_for_run(tenant_id, run_id)

    async def list_for_runs(self, tenant_id: str, run_ids: list[str]) -> list[Artifact]:
        return await self._repository.list_for_runs(tenant_id, run_ids)

    async def get(self, tenant_id: str, artifact_id: str) -> Artifact:
        return await self._repository.get(tenant_id, artifact_id)

    async def download(self, tenant_id: str, artifact_id: str) -> tuple[Artifact, bytes]:
        artifact = await self._repository.get(tenant_id, artifact_id)
        content = await self._store.get(tenant_id, artifact_id)
        return artifact, content
