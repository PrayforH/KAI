from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from harness.core.errors import ConflictError, NotFoundError
from harness.knowledge.models import (
    CreateKnowledgeBaseRequest,
    CreateKnowledgeSourceRequest,
    KnowledgeAcl,
    KnowledgeChunk,
    KnowledgeResultTrust,
    KnowledgeSnapshot,
    KnowledgeSnapshotBinding,
    KnowledgeSource,
    KnowledgeSyncRun,
    KnowledgeVisibility,
    ReplaceKnowledgeSourceRequest,
)
from harness.knowledge.repositories import InMemoryKnowledgeRepository
from harness.knowledge.search import HybridKnowledgeSearch, RankedChunk
from harness.knowledge.service import KnowledgeService


class CapturingSearch(HybridKnowledgeSearch):
    def __init__(self) -> None:
        self.visible_chunks: tuple[KnowledgeChunk, ...] = ()

    def search(
        self,
        chunks: Sequence[KnowledgeChunk],
        query: str,
        *,
        limit: int,
    ) -> tuple[RankedChunk, ...]:
        self.visible_chunks = tuple(chunks)
        return super().search(chunks, query, limit=limit)


class ConflictingSnapshotRepository(InMemoryKnowledgeRepository):
    fail_next_publish = False

    async def publish_snapshot(
        self,
        *,
        expected_source_revision: int,
        source: KnowledgeSource,
        snapshot: KnowledgeSnapshot,
        chunks: Sequence[KnowledgeChunk],
        sync: KnowledgeSyncRun,
    ) -> bool:
        if self.fail_next_publish:
            self.fail_next_publish = False
            return False
        return await super().publish_snapshot(
            expected_source_revision=expected_source_revision,
            source=source,
            snapshot=snapshot,
            chunks=chunks,
            sync=sync,
        )


def file_source(
    reference: str,
    content: str,
    *,
    acl: KnowledgeAcl | None = None,
) -> CreateKnowledgeSourceRequest:
    return CreateKnowledgeSourceRequest.model_validate(
        {
            "reference": reference,
            "displayName": reference.title(),
            "kind": "file",
            "acl": (acl or KnowledgeAcl()).model_dump(mode="json", by_alias=True),
            "config": {
                "type": "file",
                "documents": [
                    {
                        "documentId": f"{reference}-document",
                        "title": f"{reference.title()} policy",
                        "content": content,
                    }
                ],
            },
        }
    )


@pytest.mark.asyncio
async def test_sync_is_immutable_and_unchanged_content_reuses_snapshot() -> None:
    repository = InMemoryKnowledgeRepository()
    service = KnowledgeService(repository)
    source, first = await service.create_source(
        "tenant",
        "owner",
        file_source("handbook", "Annual leave is 15 days."),
    )

    assert first is not None
    assert first.status.value == "succeeded"
    first_snapshot = source.active_snapshot_id
    second = await service.sync_source("tenant", "owner", "handbook")
    refreshed = await service.get_source("tenant", "handbook")

    assert second.status.value == "unchanged"
    assert second.snapshot_id == first_snapshot
    assert refreshed.active_snapshot_id == first_snapshot
    assert len(await repository.list_snapshots("tenant")) == 1


@pytest.mark.asyncio
async def test_acl_filters_chunks_before_retrieval_scoring() -> None:
    search = CapturingSearch()
    service = KnowledgeService(InMemoryKnowledgeRepository(), search=search)
    await service.create_source(
        "tenant",
        "owner",
        file_source("public", "Public handbook says blue."),
    )
    await service.create_source(
        "tenant",
        "owner",
        file_source(
            "private",
            "Private merger code is orange.",
            acl=KnowledgeAcl(
                visibility=KnowledgeVisibility.RESTRICTED,
                userIds=("allowed-user",),
            ),
        ),
    )
    await service.create_base(
        "tenant",
        "owner",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "company",
                "displayName": "Company",
                "sourceReferences": ["public", "private"],
            }
        ),
    )

    result = await service.search(
        "tenant",
        "different-user",
        "private merger orange public blue",
        knowledge_base_references=("company",),
    )

    assert search.visible_chunks == ()
    assert result.hits == ()


@pytest.mark.asyncio
async def test_personal_knowledge_is_hidden_from_other_users() -> None:
    service = KnowledgeService(InMemoryKnowledgeRepository())
    await service.create_source(
        "tenant",
        "owner",
        file_source("private-source", "Private owner content."),
    )
    await service.create_base(
        "tenant",
        "owner",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "private-base",
                "displayName": "Private base",
                "sourceReferences": ["private-source"],
            }
        ),
    )

    assert [item.reference for item in await service.list_bases("tenant", "owner")] == [
        "private-base"
    ]
    assert await service.list_bases("tenant", "other-user") == ()
    assert await service.list_sources("tenant", "other-user") == ()
    with pytest.raises(NotFoundError, match="Knowledge Base not found"):
        await service.get_base("tenant", "private-base", "other-user")


@pytest.mark.asyncio
async def test_team_space_grant_allows_restricted_knowledge_only_for_bound_team() -> None:
    async def grants(
        tenant_id: str,
        actor_id: str,
        team_ids: tuple[str, ...],
        reference: str,
    ) -> bool:
        return (
            tenant_id == "tenant"
            and actor_id == "member"
            and team_ids == ("space-one",)
            and reference == "company"
        )

    service = KnowledgeService(InMemoryKnowledgeRepository(), team_grant_checker=grants)
    await service.create_source(
        "tenant",
        "owner",
        file_source(
            "private",
            "Shared investigation procedure.",
            acl=KnowledgeAcl(
                visibility=KnowledgeVisibility.RESTRICTED,
                userIds=("owner",),
            ),
        ),
    )
    await service.create_base(
        "tenant",
        "owner",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "company",
                "displayName": "Company",
                "sourceReferences": ["private"],
            }
        ),
    )

    denied = await service.search(
        "tenant", "member", "investigation", knowledge_base_references=("company",)
    )
    allowed = await service.search(
        "tenant",
        "member",
        "investigation",
        knowledge_base_references=("company",),
        team_ids=("space-one",),
    )

    assert denied.hits == ()
    assert len(allowed.hits) == 1


@pytest.mark.asyncio
async def test_direct_citation_open_cannot_bypass_source_acl() -> None:
    service = KnowledgeService(InMemoryKnowledgeRepository())
    source, _ = await service.create_source(
        "tenant",
        "owner",
        file_source(
            "private",
            "Confidential board material.",
            acl=KnowledgeAcl(
                visibility=KnowledgeVisibility.RESTRICTED,
                userIds=("allowed-user",),
            ),
        ),
    )
    assert source.active_snapshot_id is not None
    chunks = await service.repository.list_chunks(
        "tenant",
        frozenset({source.active_snapshot_id}),
    )

    with pytest.raises(NotFoundError, match="citation"):
        await service.get_visible_chunk(
            "tenant",
            "different-user",
            source.active_snapshot_id,
            chunks[0].chunk_id,
        )


@pytest.mark.asyncio
async def test_session_binding_remains_on_old_snapshot_after_refresh() -> None:
    service = KnowledgeService(InMemoryKnowledgeRepository())
    source, _ = await service.create_source(
        "tenant",
        "owner",
        file_source("handbook", "Policy version one has apples."),
    )
    await service.create_base(
        "tenant",
        "owner",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "company",
                "displayName": "Company",
                "sourceReferences": ["handbook"],
            }
        ),
    )
    bindings = await service.resolve_bindings("tenant", "owner", ("company",))
    old_snapshot = source.active_snapshot_id
    current = await service.get_source("tenant", "handbook")
    await service.replace_source(
        "tenant",
        "owner",
        "handbook",
        ReplaceKnowledgeSourceRequest.model_validate(
            {
                "expectedRevision": current.revision,
                "displayName": current.display_name,
                "description": current.description,
                "acl": current.acl.model_dump(mode="json", by_alias=True),
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "handbook-document",
                            "title": "Handbook policy",
                            "content": "Policy version two has pears.",
                        }
                    ],
                },
            }
        ),
    )
    await service.sync_source("tenant", "owner", "handbook")

    old_result = await service.search(
        "tenant",
        "owner",
        "apples",
        bindings=bindings,
    )
    new_result = await service.search(
        "tenant",
        "owner",
        "pears",
        knowledge_base_references=("company",),
    )

    assert bindings[0].snapshot_id == old_snapshot
    assert old_result.hits[0].citation.snapshot_id == old_snapshot
    assert new_result.hits[0].citation.snapshot_id != old_snapshot


@pytest.mark.asyncio
async def test_pinned_binding_does_not_bypass_later_acl_revocation() -> None:
    service = KnowledgeService(InMemoryKnowledgeRepository())
    source, _ = await service.create_source(
        "tenant",
        "owner",
        file_source("restricted", "A confidential operational fact."),
    )
    assert source.active_snapshot_id is not None
    binding = KnowledgeSnapshotBinding(
        knowledgeBaseReference="company",
        sourceReference="restricted",
        snapshotId=source.active_snapshot_id,
        trust=KnowledgeResultTrust.SENSITIVE,
    )
    current = await service.get_source("tenant", "restricted")
    await service.replace_source(
        "tenant",
        "owner",
        "restricted",
        ReplaceKnowledgeSourceRequest(
            expectedRevision=current.revision,
            displayName=current.display_name,
            description=current.description,
            config=current.config,
            acl=KnowledgeAcl(
                visibility=KnowledgeVisibility.RESTRICTED,
                userIds=("another-user",),
            ),
        ),
    )

    result = await service.search(
        "tenant",
        "revoked-user",
        "confidential",
        bindings=(binding,),
    )

    assert result.hits == ()
    assert result.searched_snapshot_ids == ()


@pytest.mark.asyncio
async def test_disabled_source_cannot_sync() -> None:
    service = KnowledgeService(InMemoryKnowledgeRepository())
    await service.create_source(
        "tenant",
        "owner",
        file_source("handbook", "Some text."),
    )
    current = await service.get_source("tenant", "handbook")
    await service.replace_source(
        "tenant",
        "owner",
        "handbook",
        ReplaceKnowledgeSourceRequest(
            expectedRevision=current.revision,
            displayName=current.display_name,
            description=current.description,
            config=current.config,
            acl=current.acl,
            enabled=False,
        ),
    )

    with pytest.raises(ConflictError, match="disabled"):
        await service.sync_source("tenant", "owner", "handbook")


@pytest.mark.asyncio
async def test_compare_and_set_conflict_does_not_mark_source_degraded() -> None:
    repository = ConflictingSnapshotRepository()
    service = KnowledgeService(repository)
    await service.create_source(
        "tenant",
        "owner",
        file_source("handbook", "Initial source content."),
    )
    current = await service.get_source("tenant", "handbook")
    await service.replace_source(
        "tenant",
        "owner",
        "handbook",
        ReplaceKnowledgeSourceRequest.model_validate(
            {
                "expectedRevision": current.revision,
                "displayName": current.display_name,
                "description": current.description,
                "acl": current.acl.model_dump(mode="json", by_alias=True),
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "handbook-document",
                            "title": "Handbook policy",
                            "content": "A newer source revision.",
                        }
                    ],
                },
            }
        ),
    )
    repository.fail_next_publish = True

    with pytest.raises(ConflictError, match="snapshot was published"):
        await service.sync_source("tenant", "owner", "handbook")

    source = await service.get_source("tenant", "handbook")
    syncs = await repository.list_syncs("tenant")
    assert source.health.value == "pending"
    assert source.revision == current.revision + 1
    assert syncs[0].status.value == "failed"


def test_citation_and_snapshot_hashes_are_stable() -> None:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    chunk = KnowledgeChunk(
        tenantId="tenant",
        snapshotId="snapshot",
        sourceReference="source",
        chunkId="chunk",
        documentId="document",
        ordinal=0,
        title="Title",
        sourceUri="knowledge://file/document",
        content="Stable content",
        contentHash="0" * 64,
        tokenTerms=("stable", "content"),
        createdAt=now,
    )

    assert KnowledgeSnapshot.digest_chunks((chunk,)) == (
        "c5b6295bc9c51d3b0d4c7ce96f1fc2e21ff54457c85b09f572e333c6ab04abc1"
    )
