from datetime import UTC, datetime

import pytest

from harness.adapters.memory import InMemoryArtifactStore, InMemoryInputArtifactRepository
from harness.application.input_artifacts import InputArtifactService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import ArtifactStatus


def build_service(
    *,
    max_file_bytes: int = 8,
    max_files_per_run: int = 2,
    max_total_bytes: int = 12,
) -> InputArtifactService:
    sequence = 0

    def ids(prefix: str) -> str:
        nonlocal sequence
        sequence += 1
        return f"{prefix}_{sequence}"

    return InputArtifactService(
        repository=InMemoryInputArtifactRepository(),
        store=InMemoryArtifactStore(),
        id_generator=ids,
        clock=lambda: datetime(2026, 7, 13, tzinfo=UTC),
        max_file_bytes=max_file_bytes,
        max_files_per_run=max_files_per_run,
        max_total_bytes=max_total_bytes,
    )


@pytest.mark.asyncio
async def test_channel_retries_reuse_same_owned_file_and_reject_changed_bytes():
    service = build_service()
    args = dict(tenant_id="tenant", user_id="alice", name="test.txt",
                media_type="text/plain", content=b"hello", idempotency_key="event:file")
    first = await service.upload(**args)
    assert await service.upload(**args) == first
    other_owner = await service.upload(**{**args, "user_id": "bob"})
    assert other_owner.input_artifact_id != first.input_artifact_id
    with pytest.raises(ConflictError, match="content has changed"):
        await service.upload(**{**args, "content": b"other"})
    # Recover an interrupted upload after the object was stored but its READY
    # update was rolled back; the channel never accumulates duplicate objects.
    await service._repository.update(first.model_copy(update={"status": ArtifactStatus.PENDING}))
    recovered = await service.upload(**args)
    assert recovered.input_artifact_id == first.input_artifact_id
    assert recovered.status is ArtifactStatus.READY


@pytest.mark.asyncio
async def test_upload_records_ready_metadata_and_downloads_for_owner() -> None:
    service = build_service()

    uploaded = await service.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="notes.txt",
        media_type="text/plain",
        content=b"hello",
    )

    assert uploaded.input_artifact_id == "input_artifact_1"
    assert uploaded.status is ArtifactStatus.READY
    assert uploaded.size_bytes == 5
    assert uploaded.sha256 is not None
    metadata, content = await service.download(
        tenant_id="tenant-a",
        user_id="user-1",
        input_artifact_id=uploaded.input_artifact_id,
    )
    assert metadata == uploaded
    assert content == b"hello"


@pytest.mark.asyncio
async def test_upload_rejects_file_above_limit_before_storing() -> None:
    service = build_service(max_file_bytes=4)

    with pytest.raises(ConflictError, match="maximum size"):
        await service.upload(
            tenant_id="tenant-a",
            user_id="user-1",
            name="large.txt",
            media_type="text/plain",
            content=b"12345",
        )


@pytest.mark.asyncio
async def test_resolve_rejects_cross_user_reference_without_disclosing_it() -> None:
    service = build_service()
    uploaded = await service.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="private.txt",
        media_type="text/plain",
        content=b"secret",
    )

    with pytest.raises(NotFoundError, match="input artifact not found"):
        await service.resolve_for_run(
            tenant_id="tenant-a",
            user_id="user-2",
            input_artifact_ids=[uploaded.input_artifact_id],
        )


@pytest.mark.asyncio
async def test_resolve_deduplicates_ids_and_preserves_first_seen_order() -> None:
    service = build_service()
    first = await service.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="first.txt",
        media_type="text/plain",
        content=b"first",
    )
    second = await service.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="second.txt",
        media_type="text/plain",
        content=b"second",
    )

    resolved = await service.resolve_for_run(
        tenant_id="tenant-a",
        user_id="user-1",
        input_artifact_ids=[
            first.input_artifact_id,
            second.input_artifact_id,
            first.input_artifact_id,
        ],
    )

    assert [item.input_artifact_id for item in resolved] == [
        first.input_artifact_id,
        second.input_artifact_id,
    ]


@pytest.mark.asyncio
async def test_resolve_enforces_count_and_total_size_limits() -> None:
    service = build_service(max_files_per_run=1, max_total_bytes=7)
    first = await service.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="first.txt",
        media_type="text/plain",
        content=b"1234",
    )
    second = await service.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="second.txt",
        media_type="text/plain",
        content=b"5678",
    )

    with pytest.raises(ConflictError, match="at most 1"):
        await service.resolve_for_run(
            tenant_id="tenant-a",
            user_id="user-1",
            input_artifact_ids=[first.input_artifact_id, second.input_artifact_id],
        )

    total_limited = build_service(max_files_per_run=2, max_total_bytes=7)
    third = await total_limited.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="third.txt",
        media_type="text/plain",
        content=b"1234",
    )
    fourth = await total_limited.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="fourth.txt",
        media_type="text/plain",
        content=b"5678",
    )
    with pytest.raises(ConflictError, match="total size"):
        await total_limited.resolve_for_run(
            tenant_id="tenant-a",
            user_id="user-1",
            input_artifact_ids=[third.input_artifact_id, fourth.input_artifact_id],
        )
