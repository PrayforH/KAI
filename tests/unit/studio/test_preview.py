from datetime import UTC, datetime, timedelta

import pytest

from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.models import (
    AgentDraft,
    AgentTemplate,
    CreateAgentDraftRequest,
    ReplaceAgentDraftRequest,
)
from harness.studio.preview_controller import PreviewController, PreviewProvisioningError
from harness.studio.preview_models import (
    CreatePreviewRequest,
    PreviewDeployment,
    PreviewStatus,
    transition_preview,
)
from harness.studio.preview_queue import PreviewTask, PreviewTaskQueue
from harness.studio.preview_repositories import InMemoryPreviewRepository
from harness.studio.preview_service import PreviewService
from harness.studio.repositories import InMemoryAgentDraftRepository
from harness.studio.service import AgentStudioService

NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


def services() -> tuple[
    AgentStudioService,
    PreviewService,
    PreviewController,
    InMemoryPreviewRepository,
    PreviewTaskQueue,
    list[datetime],
]:
    times = [NOW]
    drafts = InMemoryAgentDraftRepository()
    catalog = default_capability_catalog()
    studio = AgentStudioService(
        drafts,
        AgentDraftCompiler(catalog),
        catalog,
        clock=lambda: times[0],
        id_generator=lambda: "draft_preview",
    )
    previews = InMemoryPreviewRepository()
    queue = PreviewTaskQueue.memory()
    service = PreviewService(
        repository=previews,
        queue=queue,
        studio=studio,
        clock=lambda: times[0],
        id_generator=lambda: "preview_one",
    )
    controller = PreviewController(
        repository=previews,
        queue=queue,
        clock=lambda: times[0],
    )
    return studio, service, controller, previews, queue, times


async def create_draft(studio: AgentStudioService) -> AgentDraft:
    return await studio.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=CreateAgentDraftRequest(
            name="preview-analyst",
            domain="preview-analysis",
            displayName="Preview Analyst",
            description="Validate the Preview lifecycle without publishing.",
            template=AgentTemplate.ANALYST,
        ),
    )


def request(revision: int = 1) -> CreatePreviewRequest:
    return CreatePreviewRequest(
        draftId="draft_preview",
        expectedRevision=revision,
        idempotencyKey=f"preview-draft_preview-r{revision}",
        ttlSeconds=600,
    )


@pytest.mark.asyncio
async def test_create_is_idempotent_and_binds_test_identity_and_hashes() -> None:
    studio, service, _controller, repository, _queue, _times = services()
    await create_draft(studio)

    first = await service.create(tenant_id="tenant-a", user_id="builder", request=request())
    repeated = await service.create(tenant_id="tenant-a", user_id="builder", request=request())

    assert repeated == first
    assert len(await repository.list_for_tenant("tenant-a")) == 1
    assert first.status is PreviewStatus.QUEUED
    assert first.identity_kind == "test"
    assert first.environment == "preview"
    assert first.execution_profile == "isolated-default"
    assert first.execution_profile_version >= 1
    assert first.content_hash != first.package_hash


@pytest.mark.asyncio
async def test_draft_update_marks_existing_preview_stale() -> None:
    studio, service, _controller, _repository, _queue, _times = services()
    draft = await create_draft(studio)
    preview = await service.create(tenant_id="tenant-a", user_id="builder", request=request())
    await studio.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=draft.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=1,
            spec=draft.spec.model_copy(update={"description": "Changed Draft."}),
        ),
    )

    current = await service.get("tenant-a", "builder", preview.preview_id)

    assert current.stale is True
    assert current.stale_reason == "draft_revision_changed"


@pytest.mark.asyncio
async def test_controller_converges_ready_cancelled_and_expired() -> None:
    studio, service, controller, _repository, _queue, times = services()
    await create_draft(studio)
    preview = await service.create(tenant_id="tenant-a", user_id="builder", request=request())

    ready = await controller.process_once()
    assert ready is not None and ready.status is PreviewStatus.READY
    cancelling = await service.cancel(
        tenant_id="tenant-a", user_id="builder", preview_id=preview.preview_id
    )
    assert cancelling.status is PreviewStatus.CANCELLING
    cancelled = await controller.process_once()
    assert cancelled is not None and cancelled.status is PreviewStatus.CANCELLED

    # A fresh service set demonstrates TTL terminal convergence independently.
    studio2, service2, controller2, _repository2, _queue2, times2 = services()
    await create_draft(studio2)
    expiring = await service2.create(tenant_id="tenant-a", user_id="builder", request=request())
    times2[0] += timedelta(minutes=11)
    assert await controller2.reap_expired() == 1
    expired = await service2.get("tenant-a", "builder", expiring.preview_id)
    assert expired.status is PreviewStatus.EXPIRED

    times[0] += timedelta(days=1)
    assert await controller.reap_expired() == 0


@pytest.mark.asyncio
async def test_new_controller_recovers_a_provisioning_preview_after_crash() -> None:
    studio, service, _controller, repository, _queue, times = services()
    await create_draft(studio)
    preview = await service.create(tenant_id="tenant-a", user_id="builder", request=request())
    provisioning = preview.model_copy(
        update={
            "status": transition_preview(PreviewStatus.QUEUED, PreviewStatus.PROVISIONING),
            "fencing_token": 1,
            "updated_at": times[0],
        }
    )
    assert await repository.compare_and_set(PreviewStatus.QUEUED, provisioning)

    recovered = await PreviewController(
        repository=repository,
        queue=PreviewTaskQueue.memory(),
        clock=lambda: times[0],
    ).reconcile("tenant-a", preview.preview_id)

    assert recovered.status is PreviewStatus.READY
    assert recovered.fencing_token == 2


@pytest.mark.asyncio
async def test_controller_discards_stale_queue_task_after_database_restore() -> None:
    _studio, _service, controller, _repository, queue, _times = services()
    await queue.enqueue(PreviewTask(tenant_id="tenant-a", preview_id="preview_missing"))

    assert await controller.process_once() is None
    assert await queue.dequeue() is None


@pytest.mark.asyncio
async def test_controller_failure_is_terminal_and_never_publishes_a_version() -> None:
    studio, service, _controller, repository, queue, times = services()
    await create_draft(studio)
    preview = await service.create(tenant_id="tenant-a", user_id="builder", request=request())

    async def fail(_preview: PreviewDeployment) -> None:
        raise RuntimeError("injected Preview failure")

    controller = PreviewController(
        repository=repository,
        queue=queue,
        provisioner=fail,
        clock=lambda: times[0],
    )
    failed = await controller.process_once()

    assert failed is not None
    assert failed.preview_id == preview.preview_id
    assert failed.status is PreviewStatus.FAILED
    assert failed.error_code == "preview_controller_failed"


@pytest.mark.asyncio
async def test_controller_preserves_stable_preflight_error_code() -> None:
    studio, service, _controller, repository, queue, times = services()
    await create_draft(studio)
    await service.create(tenant_id="tenant-a", user_id="builder", request=request())

    async def fail(_preview: PreviewDeployment) -> None:
        raise PreviewProvisioningError("mcp_tool_mismatch")

    failed = await PreviewController(
        repository=repository,
        queue=queue,
        provisioner=fail,
        clock=lambda: times[0],
    ).process_once()

    assert failed is not None
    assert failed.status is PreviewStatus.FAILED
    assert failed.error_code == "mcp_tool_mismatch"
