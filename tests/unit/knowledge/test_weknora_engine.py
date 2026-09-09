"""Unit tests for the WeKnora engine gateway and its HTTP client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from harness.knowledge.ports import KnowledgeEngineError
from harness.knowledge.weknora.client import WeknoraClient
from harness.knowledge.weknora.configuration import WeknoraSettings
from harness.knowledge.weknora.gateway import WeknoraKnowledgeEngine


def make_client(
    handler: Any,
    *,
    base_url: str = "http://weknora.test",
) -> WeknoraClient:
    settings = WeknoraSettings(
        base_url=base_url,
        email="svc@axis.test",
        password=SecretStr("secret-pass"),
    )
    transport = httpx.MockTransport(handler)
    client = WeknoraClient(settings)
    client._client = httpx.AsyncClient(  # noqa: SLF001 - test seam
        base_url=f"{base_url}/api/v1",
        transport=transport,
        timeout=httpx.Timeout(5),
    )
    return client


def login_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"success": True, "token": "jwt-token", "refresh_token": "refresh"},
    )


@pytest.mark.asyncio
async def test_client_logs_in_and_unwraps_envelope() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        assert request.headers["Authorization"] == "Bearer jwt-token"
        return httpx.Response(
            200,
            json={"success": True, "data": [{"id": "kb-1", "name": "demo"}]},
        )

    client = make_client(handler)
    try:
        payload = await client.get_data("/knowledge-bases")
    finally:
        await client.aclose()
    assert payload == [{"id": "kb-1", "name": "demo"}]
    assert calls == ["/api/v1/auth/login", "/api/v1/knowledge-bases"]


@pytest.mark.asyncio
async def test_client_reauthenticates_once_on_401() -> None:
    logins: list[str] = []
    data_calls: list[int] = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            logins.append("login")
            return login_response()
        data_calls[0] += 1
        if data_calls[0] == 1:
            # Simulate an expired cached token on the first data request.
            return httpx.Response(401, json={"error": {"message": "expired"}})
        assert request.headers["Authorization"] == "Bearer jwt-token"
        return httpx.Response(200, json={"data": []})

    client = make_client(handler)
    try:
        await client.get_data("/knowledge-bases/kb-1/knowledge")
    finally:
        await client.aclose()
    assert logins == ["login", "login"]
    assert data_calls[0] == 2


@pytest.mark.asyncio
async def test_client_surfaces_error_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        return httpx.Response(
            200,
            json={"success": False, "error": {"message": "query_text is required"}},
        )

    client = make_client(handler)
    try:
        with pytest.raises(Exception, match="query_text is required"):
            await client.hybrid_search("kb-1", "q", limit=5)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_search_normalizes_scores_and_titles() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        assert request.url.path == "/api/v1/knowledge-bases/kb-1/hybrid-search"
        body = json.loads(request.content)
        assert body["query_text"] == "非法集资"
        assert body["match_count"] == 5
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "id": "chunk-1",
                        "knowledge_id": "doc-1",
                        "knowledge_title": "起诉书.pdf",
                        "content": "片段一",
                        "score": 0.02,
                        "knowledge_base_id": "kb-1",
                    },
                    {
                        "id": "chunk-2",
                        "knowledge_id": "doc-2",
                        "knowledge_title": "判决书.pdf",
                        "content": "片段二",
                        "score": 0.01,
                        "knowledge_base_id": "kb-1",
                    },
                ],
            },
        )

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(WeknoraSettings(base_url="http://weknora.test"), client)
    try:
        hits = await engine.search(["kb-1"], "非法集资", limit=5)
    finally:
        await engine.aclose()
    assert [hit.chunk_id for hit in hits] == ["chunk-1", "chunk-2"]
    # Raw engine scores are preserved; the service normalizes to 0..1.
    assert hits[0].score == pytest.approx(0.02)
    assert hits[1].score == pytest.approx(0.01)
    assert hits[0].document_title == "起诉书.pdf"


@pytest.mark.asyncio
async def test_gateway_create_base_maps_kb_type_strategy() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"success": True, "data": {"id": "kb-new", "name": bodies[-1]["name"]}},
        )

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(
        WeknoraSettings(
            base_url="http://weknora.test",
            embedding_model="builtin-bge-m3-v2",
            summary_model_id="summary-1",
            wiki_synthesis_model_id="wiki-1",
        ),
        client,
    )
    try:
        base_id = await engine.create_base(
            name="混合库",
            description="",
            kb_type="hybrid",
        )
        with pytest.raises(KnowledgeEngineError):
            await engine.create_base(name="x", description="", kb_type="bogus")
    finally:
        await engine.aclose()
    assert base_id == "kb-new"
    body = bodies[0]
    # WeKnora binds the create request flat; nesting under "config" silently
    # drops embedding_model_id and leaves the base unable to parse documents.
    assert "config" not in body
    assert body["embedding_model_id"] == "builtin-bge-m3-v2"
    assert body["indexing_strategy"] == {
        "vector_enabled": True,
        "keyword_enabled": True,
        "wiki_enabled": True,
        "graph_enabled": True,
    }
    # Wiki page synthesis needs an explicit model, otherwise WeKnora creates the
    # base but never generates any wiki pages.
    assert body["summary_model_id"] == "summary-1"
    assert body["wiki_config"] == {
        "synthesis_model_id": "wiki-1",
        "max_pages_per_ingest": 12,
    }


@pytest.mark.asyncio
async def test_gateway_rag_base_omits_wiki_config() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"id": "kb-rag"}})

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(
        WeknoraSettings(
            base_url="http://weknora.test",
            wiki_synthesis_model_id="wiki-1",
        ),
        client,
    )
    try:
        await engine.create_base(name="RAG 库", description="", kb_type="rag")
    finally:
        await engine.aclose()
    body = bodies[0]
    assert "wiki_config" not in body
    assert body["indexing_strategy"] == {
        "vector_enabled": True,
        "keyword_enabled": True,
    }


@pytest.mark.asyncio
async def test_gateway_manual_document_publishes_to_trigger_parsing() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "data": {"id": "doc-9"}})

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(WeknoraSettings(base_url="http://weknora.test"), client)
    try:
        doc_id = await engine.create_manual_document("kb-1", title="t", content="c")
    finally:
        await engine.aclose()
    assert doc_id == "doc-9"
    assert bodies[0]["status"] == "publish"


@pytest.mark.asyncio
async def test_gateway_list_documents_maps_statuses() -> None:
    rows: list[dict[str, Any]] = [
        {
            "id": "doc-1",
            "title": "手册.pdf",
            "parse_status": "completed",
            "summary_status": "completed",
            "file_type": "pdf",
            "file_size": 123,
            "enable_status": "enabled",
            "knowledge_base_id": "kb-1",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        return httpx.Response(200, json={"success": True, "data": rows})

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(WeknoraSettings(base_url="http://weknora.test"), client)
    try:
        documents = await engine.list_documents("kb-1")
        assert documents[0].title == "手册.pdf"
        assert documents[0].parse_status == "completed"
        rows.append({"id": "", "title": "broken"})
        with pytest.raises(KnowledgeEngineError):
            await engine.list_documents("kb-1")
    finally:
        await engine.aclose()


@pytest.mark.asyncio
async def test_gateway_wiki_graph_parses_nodes_and_edges() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "nodes": [
                        {
                            "slug": "summary/s1",
                            "title": "案例一",
                            "page_type": "summary",
                            "link_count": 19,
                        },
                        {
                            "slug": "entity/e1",
                            "title": "某公司",
                            "page_type": "entity",
                            "link_count": 3,
                        },
                        {"slug": "broken", "page_type": "concept", "link_count": 1},
                    ],
                    "edges": [{"source": "entity/e1", "target": "summary/s1"}],
                    "meta": {"total": 3, "returned": 3, "truncated": False},
                },
            },
        )

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(WeknoraSettings(base_url="http://weknora.test"), client)
    try:
        graph = await engine.wiki_graph("kb-1")
    finally:
        await engine.aclose()
    assert len(graph.nodes) == 3
    assert graph.nodes[0].page_type == "summary"
    assert graph.nodes[0].link_count == 19
    assert graph.links == (("entity/e1", "summary/s1"),)


@pytest.mark.asyncio
async def test_gateway_wiki_pages_and_stats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response()
        if request.url.path.endswith("/wiki/pages"):
            # The real endpoint returns a paginated envelope at the top level.
            return httpx.Response(
                200,
                json={
                    "pages": [
                        {
                            "slug": "summary/abc",
                            "title": "以充值油卡为名的非法集资",
                            "page_type": "summary",
                            "content": "# 标题\n内容",
                            "summary": "摘要",
                            "aliases": ["油卡案"],
                            "category_path": ["案例"],
                        }
                    ],
                    "total": 1,
                    "page": 1,
                    "page_size": 100,
                    "total_pages": 1,
                },
            )
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "total_pages": 19,
                    "pages_by_type": {"concept": 14, "entity": 2, "index": 1, "summary": 2},
                    "total_links": 42,
                },
            },
        )

    client = make_client(handler)
    engine = WeknoraKnowledgeEngine(WeknoraSettings(base_url="http://weknora.test"), client)
    try:
        pages = await engine.list_wiki_pages("kb-1")
        stats = await engine.wiki_stats("kb-1")
    finally:
        await engine.aclose()
    assert len(pages) == 1
    assert pages[0].page_type == "summary"
    assert pages[0].aliases == ("油卡案",)
    assert stats.total_pages == 19
    assert stats.pages_by_type["summary"] == 2
