"""Service dispatch tests for engine-backed (WeKnora) knowledge bases."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from harness.knowledge.directory import DirectoryUser, InMemoryUserDirectory
from harness.knowledge.models import (
    AddKnowledgeMembersRequest,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeSourceRequest,
    KnowledgeBaseEngine,
    KnowledgeDocumentStatus,
    KnowledgeMemberRole,
)
from harness.knowledge.ports import (
    EngineBaseConfig,
    EngineChunk,
    EngineDocumentStatus,
    EngineSearchHit,
    EngineWikiGraph,
    EngineWikiGraphNode,
    EngineWikiPage,
    EngineWikiStats,
    KnowledgeEngineError,
    KnowledgeEngineNotConfiguredError,
)
from harness.knowledge.repositories import InMemoryKnowledgeRepository
from harness.knowledge.service import KnowledgeService


class FakeEngine:
    def __init__(self) -> None:
        self.created_bases: list[dict[str, str]] = []
        self.deleted_bases: list[str] = []
        self.fail_delete_base_ids: set[str] = set()
        self.search_calls: list[tuple[list[str], str]] = []
        self.documents: dict[str, list[EngineDocumentStatus]] = {}
        self.document_rows: list[EngineDocumentStatus] = [
            EngineDocumentStatus(
                document_id="doc-1",
                title="手册.pdf",
                parse_status="completed",
                summary_status="completed",
                knowledge_base_id="remote-1",
            )
        ]
        self.fail_base_ids: set[str] = set()

    async def create_base(
        self,
        *,
        name: str,
        description: str,
        kb_type: str,
        config: EngineBaseConfig | None = None,
    ) -> str:
        self.created_bases.append(
            {"name": name, "description": description, "kb_type": kb_type, "config": config}
        )
        return "remote-1"

    async def delete_base(self, base_id: str) -> None:
        if base_id in self.fail_delete_base_ids:
            raise KnowledgeEngineError("remote unavailable")
        self.deleted_bases.append(base_id)

    async def list_documents(self, base_id: str) -> tuple[EngineDocumentStatus, ...]:
        if base_id in self.fail_base_ids:
            raise KnowledgeEngineError("remote unavailable")
        return self.documents.get(base_id, tuple(self.document_rows))

    async def create_manual_document(
        self,
        base_id: str,
        *,
        title: str,
        content: str,
    ) -> str:
        return "doc-manual"

    async def upload_document(
        self,
        base_id: str,
        *,
        filename: str,
        content: bytes,
    ) -> str:
        return "doc-file"

    async def delete_document(self, base_id: str, document_id: str) -> None:
        return None

    async def reparse_document(self, base_id: str, document_id: str) -> None:
        return None

    async def get_document(self, document_id: str) -> EngineDocumentStatus:
        return EngineDocumentStatus(
            document_id=document_id,
            title="手册.pdf",
            parse_status="completed",
            knowledge_base_id="remote-1",
        )

    async def list_chunks(self, document_id: str) -> tuple[EngineChunk, ...]:
        return (
            EngineChunk(
                chunk_id="chunk-1",
                document_id=document_id,
                content="切片内容",
                seq=0,
                knowledge_base_id="remote-1",
            ),
        )

    async def get_chunk(self, chunk_id: str) -> EngineChunk | None:
        if chunk_id != "chunk-1":
            return None
        return EngineChunk(
            chunk_id=chunk_id,
            document_id="doc-1",
            content="切片内容",
            knowledge_base_id="remote-1",
        )

    async def search(
        self,
        base_ids: Sequence[str],
        query: str,
        *,
        limit: int,
    ) -> tuple[EngineSearchHit, ...]:
        self.search_calls.append((list(base_ids), query))
        return (
            EngineSearchHit(
                chunk_id="chunk-1",
                document_id="doc-1",
                document_title="手册.pdf",
                content="命中内容",
                score=0.9,
                knowledge_base_id="remote-1",
            ),
        )

    async def list_wiki_pages(self, base_id: str) -> tuple[EngineWikiPage, ...]:
        return (
            EngineWikiPage(
                slug="summary/doc-1",
                title="案例一",
                page_type="summary",
                content="# 案例一",
                summary="摘要",
            ),
        )

    async def get_wiki_page(self, base_id: str, slug: str) -> EngineWikiPage:
        return EngineWikiPage(
            slug=slug,
            title="案例一",
            page_type="summary",
            content="# 案例一",
        )

    async def search_wiki_pages(
        self,
        base_id: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[EngineWikiPage, ...]:
        return ()

    async def wiki_graph(self, base_id: str) -> EngineWikiGraph:
        return EngineWikiGraph(
            nodes=(
                EngineWikiGraphNode(
                    slug="summary/doc-1",
                    title="案例一",
                    page_type="summary",
                    link_count=2,
                ),
            ),
            links=(),
        )

    async def wiki_stats(self, base_id: str) -> EngineWikiStats:
        return EngineWikiStats(
            total_pages=1,
            pages_by_type={"summary": 1},
            total_links=2,
        )


def weknora_source() -> CreateKnowledgeSourceRequest:
    return CreateKnowledgeSourceRequest.model_validate(
        {
            "reference": "case-library",
            "displayName": "案例库",
            "kind": "weknora",
            "config": {"type": "weknora", "weknoraBaseId": "remote-1"},
        }
    )


def make_service(
    engine: FakeEngine | None = None,
    directory: InMemoryUserDirectory | None = None,
) -> tuple[KnowledgeService, FakeEngine]:
    service_engine = engine or FakeEngine()
    service = KnowledgeService(
        InMemoryKnowledgeRepository(),
        clock=lambda: datetime.now(UTC),
        engine=service_engine,
        directory=directory,
    )
    return service, service_engine


@pytest.mark.asyncio
async def test_create_base_with_weknora_engine_provisions_remote_base() -> None:
    service, engine = make_service()
    base = await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "cases",
                "displayName": "案例知识库",
                "engine": "weknora",
                "kbType": "hybrid",
            }
        ),
    )
    assert base.engine is KnowledgeBaseEngine.WEKNORA
    assert base.engine_ref == "remote-1"
    assert base.kb_type.value == "hybrid"
    created = engine.created_bases[0]
    assert {key: created[key] for key in ("name", "description", "kb_type")} == {
        "name": "案例知识库",
        "description": "",
        "kb_type": "hybrid",
    }
    # A wizard that sets nothing must not pin any engine-side default.
    assert created["config"] == EngineBaseConfig()


@pytest.mark.asyncio
async def test_create_base_forwards_wizard_config_to_the_engine() -> None:
    service, engine = make_service()
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "cases",
                "displayName": "案例知识库",
                "engine": "weknora",
                "kbType": "hybrid",
                "config": {
                    "chunkSize": 3200,
                    "chunkOverlap": 200,
                    "wikiGranularity": "exhaustive",
                    "wikiContentInstructions": "  用法务口吻  ",
                    "wikiExtractionInstructions": "重点识别责任主体",
                    "wikiMaxPagesPerIngest": 24,
                },
            }
        ),
    )
    assert engine.created_bases[0]["config"] == EngineBaseConfig(
        chunk_size=3200,
        chunk_overlap=200,
        wiki_granularity="exhaustive",
        wiki_content_instructions="用法务口吻",
        wiki_extraction_instructions="重点识别责任主体",
        wiki_max_pages_per_ingest=24,
    )


@pytest.mark.asyncio
async def test_weknora_source_sync_mirrors_remote_status() -> None:
    service, engine = make_service()
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {"reference": "cases", "displayName": "案例知识库", "engine": "weknora"}
        ),
    )
    source, sync = await service.create_source(
        "local",
        "user-1",
        weknora_source(),
    )
    assert sync is not None
    assert sync.status.value == "succeeded"
    assert sync.documents_seen == 1
    assert sync.checkpoint_after["documents"] == 1
    assert source.health.value == "healthy"
    assert source.active_snapshot_id is None
    # The base created in this test provisioned its own remote base.
    assert len(engine.created_bases) == 1


@pytest.mark.asyncio
async def test_weknora_source_sync_failure_degrades_source() -> None:
    service, engine = make_service()
    engine.fail_base_ids.add("remote-1")
    _source, sync = await service.create_source("local", "user-1", weknora_source())
    assert sync is not None
    assert sync.status.value == "failed"
    degraded = await service.repository.get_source("local", "case-library")
    assert degraded.health.value == "degraded"


@pytest.mark.asyncio
async def test_search_dispatches_weknora_bases_to_engine() -> None:
    service, engine = make_service()
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {"reference": "cases", "displayName": "案例知识库", "engine": "weknora"}
        ),
    )
    await service.create_source("local", "user-1", weknora_source())
    response = await service.search(
        "local",
        "user-1",
        "非法集资",
        knowledge_base_references=("cases",),
        limit=5,
    )
    assert engine.search_calls == [(["remote-1"], "非法集资")]
    assert len(response.hits) == 1
    hit = response.hits[0]
    assert hit.citation.chunk_id == "chunk-1"
    assert hit.citation.document_id == "doc-1"
    assert hit.citation.title == "手册.pdf"
    assert hit.score == pytest.approx(1.0)
    assert hit.content == "命中内容"


@pytest.mark.asyncio
async def test_create_base_requires_configured_engine() -> None:
    service = KnowledgeService(
        InMemoryKnowledgeRepository(),
        clock=lambda: datetime.now(UTC),
    )
    with pytest.raises(KnowledgeEngineNotConfiguredError):
        await service.create_base(
            "local",
            "user-1",
            CreateKnowledgeBaseRequest.model_validate(
                {"reference": "cases", "displayName": "案例知识库", "engine": "weknora"}
            ),
        )


@pytest.mark.asyncio
async def test_document_and_chunk_proxies() -> None:
    service, _engine = make_service()
    await service.create_source("local", "user-1", weknora_source())
    status = await service.create_source_document(
        "local",
        "user-1",
        "case-library",
        "新文档",
        "正文",
    )
    assert isinstance(status, KnowledgeDocumentStatus)
    assert status.document_id == "doc-manual"

    chunks = await service.list_source_chunks("local", "user-1", "case-library", "doc-1")
    assert chunks[0].chunk_id == "chunk-1"
    assert chunks[0].title == "手册.pdf"

    chunk = await service.get_source_chunk("local", "user-1", "case-library", "chunk-1")
    assert chunk.content == "切片内容"

    # Personal ACLs are RESTRICTED to the creator, so other tenants/users 404.
    from harness.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.get_source_chunk("local", "user-2", "case-library", "chunk-1")


@pytest.mark.asyncio
async def test_wiki_proxies_require_viewer_access_and_acl() -> None:
    from harness.core.errors import NotFoundError

    service, engine = make_service()
    await service.create_source("local", "user-1", weknora_source())
    # creator can reach wiki
    pages = await service.list_wiki_pages("local", "user-1", "case-library")
    assert len(pages) == 1
    assert pages[0].page_type == "summary"
    stats = await service.wiki_stats("local", "user-1", "case-library")
    assert stats.total_pages == 1
    graph = await service.wiki_graph("local", "user-1", "case-library")
    assert graph.nodes[0].slug == "summary/doc-1"

    # non-member 404s (personal ACL restricted to creator)
    with pytest.raises(NotFoundError):
        await service.wiki_graph("local", "user-2", "case-library")


async def create_engine_base(service: KnowledgeService, reference: str = "case-library") -> None:
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": reference,
                "displayName": "案例库",
                "engine": "weknora",
                "kbType": "rag",
            }
        ),
    )


@pytest.mark.asyncio
async def test_member_roles_gate_read_and_write() -> None:
    from harness.core.errors import NotFoundError

    directory = InMemoryUserDirectory(
        [
            DirectoryUser(user_id="user-2", email="viewer@axis.test", display_name="查看者"),
            DirectoryUser(user_id="user-3", email="editor@axis.test", display_name="编辑者"),
        ]
    )
    service, _engine = make_service(directory=directory)
    await create_engine_base(service)

    # No grant yet: neither user can reach the base.
    assert await service.list_bases("local", "user-2") == ()
    with pytest.raises(NotFoundError):
        await service.list_source_documents("local", "user-2", "case-library")

    # Owner grants viewer + editor in one batch (one by id, one by email).
    result = await service.add_members(
        "local",
        "user-1",
        "case-library",
        AddKnowledgeMembersRequest.model_validate(
            {"userIds": ["user-2"], "emails": ["editor@axis.test"], "role": "viewer"}
        ),
    )
    assert len(result.members) == 2
    assert result.unresolved == ()

    # Viewers can read.
    docs = await service.list_source_documents("local", "user-2", "case-library")
    assert len(docs) == 1
    assert [base.reference for base in await service.list_bases("local", "user-2")] == [
        "case-library"
    ]

    # Viewers cannot write.
    with pytest.raises(NotFoundError):
        await service.create_source_document("local", "user-2", "case-library", "t", "c")

    # Promote user-3 to editor, then writes succeed.
    editor_member = next(item for item in result.members if item.subject_id == "user-3")
    await service.update_member_role(
        "local", "user-1", "case-library", editor_member.member_id, KnowledgeMemberRole.EDITOR
    )
    created = await service.create_source_document("local", "user-3", "case-library", "t", "c")
    assert created.document_id == "doc-manual"

    # A viewer still cannot write after another member is promoted.
    with pytest.raises(NotFoundError):
        await service.create_source_document("local", "user-2", "case-library", "t", "c")

    # Removing the editor revokes write access.
    await service.remove_member("local", "user-1", "case-library", editor_member.member_id)
    with pytest.raises(NotFoundError):
        await service.create_source_document("local", "user-3", "case-library", "t", "c")


@pytest.mark.asyncio
async def test_member_batch_reports_unresolved_emails() -> None:
    directory = InMemoryUserDirectory(
        [DirectoryUser(user_id="user-2", email="viewer@axis.test", display_name="查看者")]
    )
    service, _engine = make_service(directory=directory)
    await create_engine_base(service)
    result = await service.add_members(
        "local",
        "user-1",
        "case-library",
        AddKnowledgeMembersRequest.model_validate(
            {"emails": ["viewer@axis.test", "ghost@axis.test"], "role": "viewer"}
        ),
    )
    assert len(result.members) == 1
    assert result.unresolved == ("ghost@axis.test",)


def test_healthy_weknora_source_round_trips_without_local_snapshot() -> None:
    """Postgres re-validates stored payloads; a healthy engine source has no
    local snapshot, so the model must accept that shape."""
    from harness.knowledge.models import KnowledgeSource

    payload = {
        "tenantId": "local",
        "reference": "cases",
        "displayName": "案例库",
        "description": "",
        "kind": "weknora",
        "config": {"type": "weknora", "weknoraBaseId": "remote-1"},
        "acl": {"visibility": "restricted", "userIds": ["u1"], "workloadIds": []},
        "revision": 2,
        "health": "healthy",
        "activeSnapshotId": None,
        "checkpoint": {},
        "lastSyncId": "sync-1",
        "lastSyncAt": "2026-09-08T15:39:37Z",
        "lastError": None,
        "createdBy": "u1",
        "updatedBy": "u1",
        "createdAt": "2026-09-08T15:39:00Z",
        "updatedAt": "2026-09-08T15:39:37Z",
    }
    source = KnowledgeSource.model_validate(payload)
    assert source.health.value == "healthy"
    assert source.active_snapshot_id is None

    legacy = dict(payload, kind="file")
    legacy["config"] = {
        "type": "file",
        "documents": [{"documentId": "d1", "title": "t", "content": "c"}],
    }
    with pytest.raises(ValueError, match="requires an active snapshot"):
        KnowledgeSource.model_validate(legacy)


@pytest.mark.asyncio
async def test_document_mutations_refresh_the_base_count() -> None:
    service, engine = make_service()
    await create_engine_base(service)
    bases = await service.list_bases("local", "user-1")
    assert bases[0].document_count == 1

    # A new upload must refresh the cached count, not wait for a manual sync.
    engine.document_rows.append(
        EngineDocumentStatus(
            document_id="doc-2",
            title="分类表.xlsx",
            parse_status="completed",
            knowledge_base_id="remote-1",
        )
    )
    await service.upload_source_document("local", "user-1", "case-library", "分类表.xlsx", b"data")
    bases = await service.list_bases("local", "user-1")
    assert bases[0].document_count == 2

    engine.document_rows.pop()
    await service.delete_source_document("local", "user-1", "case-library", "doc-2")
    bases = await service.list_bases("local", "user-1")
    assert bases[0].document_count == 1


@pytest.mark.asyncio
async def test_bound_wiki_preserves_same_slug_across_bases_and_global_limit() -> None:
    from unittest.mock import AsyncMock

    service, engine = make_service()
    engine.search_wiki_pages = AsyncMock(
        return_value=(
            EngineWikiPage(slug="entity/a", title="甲", page_type="entity", content="正文"),
        )
    )
    for reference in ("cases", "policy"):
        await service.create_base(
            "local",
            "user-1",
            CreateKnowledgeBaseRequest.model_validate(
                {
                    "reference": reference,
                    "displayName": reference,
                    "engine": "weknora",
                    "kbType": "hybrid",
                }
            ),
        )
    bindings = await service.resolve_bindings("local", "user-1", ("cases", "policy"), ())
    pages = await service.search_bound_wiki_pages("local", "user-1", bindings, "概念", limit=2)
    assert len(pages) == 2
    assert {page.knowledge_base_reference for page in pages} == {"cases", "policy"}
    assert pages[0].slug == pages[1].slug
    limited = await service.search_bound_wiki_pages("local", "user-1", bindings, "概念", limit=1)
    assert len(limited) == 1
    denied = await service.search_bound_wiki_pages("local", "other", bindings, "概念", limit=2)
    assert denied == []


@pytest.mark.asyncio
async def test_bound_wiki_propagates_engine_failure_instead_of_empty_evidence() -> None:
    from unittest.mock import AsyncMock

    service, engine = make_service()
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {
                "reference": "cases",
                "displayName": "Cases",
                "engine": "weknora",
                "kbType": "wiki",
            }
        ),
    )
    bindings = await service.resolve_bindings("local", "user-1", ("cases",), ())
    engine.search_wiki_pages = AsyncMock(side_effect=KnowledgeEngineError("offline"))
    with pytest.raises(KnowledgeEngineError, match="offline"):
        await service.search_bound_wiki_pages("local", "user-1", bindings, "概念")


@pytest.mark.asyncio
async def test_delete_base_removes_remote_base_and_local_rows() -> None:
    directory = InMemoryUserDirectory(
        [DirectoryUser(user_id="user-2", email="viewer@axis.test", display_name="查看者")]
    )
    service, engine = make_service(directory=directory)
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {"reference": "cases", "displayName": "案例知识库", "engine": "weknora"}
        ),
    )
    await service.add_members(
        "local",
        "user-1",
        "cases",
        AddKnowledgeMembersRequest.model_validate({"userIds": ["user-2"], "role": "viewer"}),
    )

    await service.delete_base("local", "user-1", "cases")

    assert engine.deleted_bases == ["remote-1"]
    from harness.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.repository.get_base("local", "cases")
    with pytest.raises(NotFoundError):
        await service.repository.get_source("local", "cases")
    assert await service.repository.list_members("local", knowledge_base_reference="cases") == ()


@pytest.mark.asyncio
async def test_delete_base_remote_failure_keeps_local_rows() -> None:
    service, engine = make_service()
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {"reference": "cases", "displayName": "案例知识库", "engine": "weknora"}
        ),
    )
    engine.fail_delete_base_ids.add("remote-1")

    with pytest.raises(KnowledgeEngineError):
        await service.delete_base("local", "user-1", "cases")

    # Nothing was removed locally, so the delete can be retried later.
    assert await service.repository.get_base("local", "cases") is not None
    assert await service.repository.get_source("local", "cases") is not None
    assert engine.deleted_bases == []


@pytest.mark.asyncio
async def test_delete_base_requires_editor_role() -> None:
    service, _engine = make_service()
    await service.create_base(
        "local",
        "user-1",
        CreateKnowledgeBaseRequest.model_validate(
            {"reference": "cases", "displayName": "案例知识库", "engine": "weknora"}
        ),
    )
    from harness.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await service.delete_base("local", "user-9", "cases")
