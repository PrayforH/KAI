"""Service dispatch tests for engine-backed (WeKnora) knowledge bases."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from harness.knowledge.models import (
    CreateKnowledgeBaseRequest,
    CreateKnowledgeSourceRequest,
    KnowledgeBaseEngine,
    KnowledgeDocumentStatus,
)
from harness.knowledge.ports import (
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
        self.search_calls: list[tuple[list[str], str]] = []
        self.documents: dict[str, list[EngineDocumentStatus]] = {}
        self.fail_base_ids: set[str] = set()

    async def create_base(self, *, name: str, description: str, kb_type: str) -> str:
        self.created_bases.append(
            {"name": name, "description": description, "kb_type": kb_type}
        )
        return "remote-1"

    async def delete_base(self, base_id: str) -> None:
        return None

    async def list_documents(self, base_id: str) -> tuple[EngineDocumentStatus, ...]:
        if base_id in self.fail_base_ids:
            raise KnowledgeEngineError("remote unavailable")
        return self.documents.get(
            base_id,
            (
                EngineDocumentStatus(
                    document_id="doc-1",
                    title="手册.pdf",
                    parse_status="completed",
                    summary_status="completed",
                    knowledge_base_id=base_id,
                ),
            ),
        )

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


def make_service(engine: FakeEngine | None = None) -> tuple[KnowledgeService, FakeEngine]:
    service_engine = engine or FakeEngine()
    service = KnowledgeService(
        InMemoryKnowledgeRepository(),
        clock=lambda: datetime.now(UTC),
        engine=service_engine,
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
    assert engine.created_bases == [
        {"name": "案例知识库", "description": "", "kb_type": "hybrid"}
    ]


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
