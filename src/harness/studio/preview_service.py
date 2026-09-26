"""Studio Preview creation, stale detection and cancellation use cases."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from harness.auth.audit import AuditService
from harness.core.errors import ConflictError
from harness.quota.models import QuotaResource
from harness.quota.service import QuotaService
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import DraftCompilationError
from harness.studio.models import CapabilityCatalogRecord
from harness.studio.preview_models import (
    CreatePreviewRequest,
    PreviewDeployment,
    PreviewStatus,
    transition_preview,
)
from harness.studio.preview_queue import PreviewTask, PreviewTaskQueue
from harness.studio.preview_repositories import PreviewRepository
from harness.studio.service import AgentStudioService


class PreviewService:
    def __init__(
        self,
        *,
        repository: PreviewRepository,
        queue: PreviewTaskQueue,
        studio: AgentStudioService,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[], str] | None = None,
        quotas: QuotaService | None = None,
        catalog_resolver: Callable[[str], Awaitable[CapabilityCatalogRecord]] | None = None,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._studio = studio
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_generator = id_generator or (lambda: f"preview_{uuid4().hex}")
        self._quotas = quotas
        self._catalog_resolver = catalog_resolver

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: CreatePreviewRequest,
    ) -> PreviewDeployment:
        existing = await self._repository.find_by_idempotency(
            tenant_id, user_id, request.idempotency_key
        )
        if existing is not None:
            self._ensure_same_request(existing, request)
            return await self._view(existing)

        draft = await self._studio.get(tenant_id, user_id, request.draft_id)
        if draft.revision != request.expected_revision:
            raise ConflictError(
                "Agent draft revision changed before Preview creation: "
                f"expected={request.expected_revision} actual={draft.revision}"
            )
        validation = await self._studio.validate(tenant_id, user_id, request.draft_id)
        if not validation.ready:
            raise DraftCompilationError(
                tuple(issue for issue in validation.issues if issue.severity == "error")
            )
        if validation.content_hash is None or validation.package_hash is None:
            raise RuntimeError("ready Draft validation did not produce immutable hashes")
        now = self._clock()
        catalog = (
            (await self._catalog_resolver(tenant_id)).catalog
            if self._catalog_resolver is not None else default_capability_catalog()
        )
        profile = next(
            (
                item
                for item in catalog.execution_profiles
                if item.profile_id == draft.spec.execution_profile and item.enabled
            ),
            None,
        )
        if profile is None:
            raise ConflictError(f"Execution Profile is unavailable: {draft.spec.execution_profile}")
        preview = PreviewDeployment(
            previewId=self._id_generator(),
            tenantId=tenant_id,
            draftId=request.draft_id,
            draftRevision=draft.revision,
            contentHash=validation.content_hash,
            packageHash=validation.package_hash,
            requestedBy=user_id,
            idempotencyKey=request.idempotency_key,
            status=PreviewStatus.QUEUED,
            executionProfile=profile.profile_id,
            executionProfileVersion=profile.version,
            createdAt=now,
            updatedAt=now,
            expiresAt=now + timedelta(seconds=request.ttl_seconds),
        )
        quota_subject = f"preview:{user_id}:{request.idempotency_key}"
        reservation = (
            await self._quotas.reserve(
                tenant_id=tenant_id,
                resource=QuotaResource.ACTIVE_PREVIEWS,
                amount=1,
                subject_id=quota_subject,
                idempotency_key=f"preview:{user_id}:{request.idempotency_key}:active",
                agent_name=draft.spec.name,
                environment="preview",
                ttl_seconds=request.ttl_seconds + 300,
            )
            if self._quotas is not None
            else None
        )
        try:
            await self._repository.add(preview)
        except ConflictError:
            concurrent = await self._repository.find_by_idempotency(
                tenant_id, user_id, request.idempotency_key
            )
            if concurrent is None:
                if reservation is not None:
                    await self._quotas.release(reservation)  # type: ignore[union-attr]
                raise
            self._ensure_same_request(concurrent, request)
            return await self._view(concurrent)
        except Exception:
            if reservation is not None:
                await self._quotas.release(reservation)  # type: ignore[union-attr]
            raise
        await self._queue.enqueue(PreviewTask(tenant_id=tenant_id, preview_id=preview.preview_id))
        await self._record(
            preview,
            user_id=user_id,
            action="studio.preview.create",
            outcome="success",
        )
        return preview

    async def get(self, tenant_id: str, owner_user_id: str, preview_id: str) -> PreviewDeployment:
        return await self._view(
            await self._repository.get_for_user(tenant_id, owner_user_id, preview_id)
        )

    async def list(self, tenant_id: str, owner_user_id: str) -> list[PreviewDeployment]:
        return [
            await self._view(preview)
            for preview in await self._repository.list_for_user(tenant_id, owner_user_id)
        ]

    async def cancel(self, *, tenant_id: str, user_id: str, preview_id: str) -> PreviewDeployment:
        current = await self._repository.get_for_user(tenant_id, user_id, preview_id)
        if current.status.is_terminal:
            return await self._view(current)
        if current.status is not PreviewStatus.CANCELLING:
            updated = current.model_copy(
                update={
                    "status": transition_preview(current.status, PreviewStatus.CANCELLING),
                    "updated_at": self._clock(),
                    "fencing_token": current.fencing_token + 1,
                }
            )
            if not await self._repository.compare_and_set(current.status, updated):
                raise ConflictError("Preview changed during cancellation")
            current = updated
        await self._queue.enqueue(PreviewTask(tenant_id=tenant_id, preview_id=preview_id))
        await self._record(
            current,
            user_id=user_id,
            action="studio.preview.cancel",
            outcome="success",
        )
        return await self._view(current)

    @staticmethod
    def _ensure_same_request(existing: PreviewDeployment, request: CreatePreviewRequest) -> None:
        if (
            existing.draft_id != request.draft_id
            or existing.draft_revision != request.expected_revision
        ):
            raise ConflictError(
                "Preview idempotency key was already used for another Draft revision"
            )

    async def _view(self, preview: PreviewDeployment) -> PreviewDeployment:
        draft = await self._studio.get(preview.tenant_id, preview.requested_by, preview.draft_id)
        if draft.revision != preview.draft_revision:
            return preview.model_copy(
                update={
                    "stale": True,
                    "stale_reason": "draft_revision_changed",
                }
            )
        validation = await self._studio.validate(
            preview.tenant_id, preview.requested_by, preview.draft_id
        )
        if (
            validation.content_hash != preview.content_hash
            or validation.package_hash != preview.package_hash
        ):
            return preview.model_copy(
                update={"stale": True, "stale_reason": "draft_content_changed"}
            )
        return preview.model_copy(update={"stale": False, "stale_reason": None})

    async def _record(
        self,
        preview: PreviewDeployment,
        *,
        user_id: str,
        action: str,
        outcome: str,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            tenant_id=preview.tenant_id,
            user_id=user_id,
            action=action,
            resource_type="preview_deployment",
            resource_id=preview.preview_id,
            outcome=outcome,
            details={
                "draft_id": preview.draft_id,
                "draft_revision": preview.draft_revision,
                "content_hash": preview.content_hash,
                "package_hash": preview.package_hash,
                "identity_kind": preview.identity_kind,
                "environment": preview.environment,
                "status": preview.status.value,
            },
        )
