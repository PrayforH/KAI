"""WeKnora implementation of the knowledge engine port.

The gateway translates AXIS engine calls into WeKnora REST calls and normalizes
remote payloads (status enums, score scales, chunk shapes) for the service.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from harness.knowledge.ports import (
    EngineChunk,
    EngineDocumentStatus,
    EngineSearchHit,
    EngineWikiGraph,
    EngineWikiGraphNode,
    EngineWikiPage,
    EngineWikiStats,
    KnowledgeEngineError,
)
from harness.knowledge.weknora.client import WeknoraClient, WeknoraError
from harness.knowledge.weknora.configuration import WeknoraSettings


def _text(value: Any) -> str:  # noqa: ANN401 - remote JSON payload values
    return value if isinstance(value, str) else ""


def _as_dict(value: Any) -> dict[str, Any]:  # noqa: ANN401 - remote JSON payload
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:  # noqa: ANN401 - remote JSON payload
    return cast(list[Any], value) if isinstance(value, list) else []


class WeknoraKnowledgeEngine:
    def __init__(self, settings: WeknoraSettings, client: WeknoraClient | None = None) -> None:
        self._settings = settings
        self._client = client or WeknoraClient(settings)

    async def aclose(self) -> None:
        await self._client.aclose()

    def indexing_strategy(self, kb_type: str) -> dict[str, bool]:
        switches = self._settings.kb_type_strategies.get(kb_type)
        if switches is None:
            raise KnowledgeEngineError(f"unsupported knowledge base type: {kb_type}")
        return {name: True for name in switches}

    def wiki_config(self, kb_type: str) -> dict[str, Any] | None:
        """Wiki page synthesis config; required for WeKnora to generate pages."""
        strategy = self._settings.kb_type_strategies.get(kb_type)
        if strategy is None or "wiki_enabled" not in strategy:
            return None
        if not self._settings.wiki_synthesis_model_id:
            return None
        return {
            "synthesis_model_id": self._settings.wiki_synthesis_model_id,
            "max_pages_per_ingest": self._settings.wiki_max_pages_per_ingest,
        }

    async def create_base(self, *, name: str, description: str, kb_type: str) -> str:
        try:
            payload = await self._client.create_knowledge_base(
                name=name,
                description=description,
                indexing_strategy=self.indexing_strategy(kb_type),
                embedding_model=self._settings.embedding_model,
                summary_model_id=self._settings.summary_model_id,
                wiki_config=self.wiki_config(kb_type),
            )
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora create base failed: {error}") from error
        return str(payload["id"])

    async def delete_base(self, base_id: str) -> None:
        try:
            await self._client.delete_knowledge_base(base_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora delete base failed: {error}") from error

    async def list_documents(self, base_id: str) -> tuple[EngineDocumentStatus, ...]:
        try:
            rows = await self._client.list_documents(base_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora list documents failed: {error}") from error
        return tuple(_document_status(row) for row in rows)

    async def get_document(self, document_id: str) -> EngineDocumentStatus:
        try:
            row = await self._client.get_document(document_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora get document failed: {error}") from error
        return _document_status(row)

    async def create_manual_document(
        self,
        base_id: str,
        *,
        title: str,
        content: str,
    ) -> str:
        try:
            row = await self._client.create_manual_document(base_id, title=title, content=content)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora create document failed: {error}") from error
        return str(row["id"])

    async def upload_document(
        self,
        base_id: str,
        *,
        filename: str,
        content: bytes,
    ) -> str:
        try:
            row = await self._client.upload_document(
                base_id,
                filename=filename,
                content=content,
            )
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora upload document failed: {error}") from error
        return str(row["id"])

    async def delete_document(self, base_id: str, document_id: str) -> None:
        try:
            await self._client.delete_document(document_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora delete document failed: {error}") from error

    async def reparse_document(self, base_id: str, document_id: str) -> None:
        try:
            await self._client.reparse_document(document_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora reparse failed: {error}") from error

    async def list_chunks(self, document_id: str) -> tuple[EngineChunk, ...]:
        try:
            rows = await self._client.list_chunks(document_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora list chunks failed: {error}") from error
        chunks: list[EngineChunk] = []
        for row in rows:
            item = _chunk(row)
            if item is not None:
                chunks.append(item)
        return tuple(chunks)

    async def get_chunk(self, chunk_id: str) -> EngineChunk | None:
        try:
            row = await self._client.get_chunk(chunk_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora get chunk failed: {error}") from error
        return _chunk(row) if row else None

    async def search(
        self,
        base_ids: Sequence[str],
        query: str,
        *,
        limit: int,
    ) -> tuple[EngineSearchHit, ...]:
        hits: list[EngineSearchHit] = []
        for base_id in base_ids:
            try:
                rows = await self._client.hybrid_search(base_id, query, limit=limit)
            except WeknoraError as error:
                raise KnowledgeEngineError(f"weknora search failed: {error}") from error
            hits.extend(_hit(row, base_id) for row in rows)
        hits.sort(key=lambda item: item.score, reverse=True)
        return tuple(hits[:limit])

    async def list_wiki_pages(self, base_id: str) -> tuple[EngineWikiPage, ...]:
        try:
            rows = await self._client.list_wiki_pages(base_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora wiki pages failed: {error}") from error
        pages: list[EngineWikiPage] = []
        for row in rows:
            item = _wiki_page(row)
            if item is not None:
                pages.append(item)
        return tuple(pages)

    async def get_wiki_page(self, base_id: str, slug: str) -> EngineWikiPage:
        try:
            row = await self._client.get_wiki_page(base_id, slug)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora wiki page failed: {error}") from error
        page = _wiki_page(row)
        if page is None:
            raise KnowledgeEngineError("weknora wiki page is missing a slug")
        return page

    async def search_wiki_pages(
        self,
        base_id: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[EngineWikiPage, ...]:
        try:
            rows = await self._client.search_wiki_pages(base_id, query, limit=limit)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora wiki search failed: {error}") from error
        pages: list[EngineWikiPage] = []
        for row in rows:
            item = _wiki_page(row)
            if item is not None:
                pages.append(item)
        return tuple(pages)

    async def wiki_graph(self, base_id: str) -> EngineWikiGraph:
        try:
            row = await self._client.get_wiki_graph(base_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora wiki graph failed: {error}") from error
        payload = _as_dict(row)
        nodes = _as_list(payload.get("nodes"))
        edges = _as_list(payload.get("edges"))
        graph_nodes: list[EngineWikiGraphNode] = []
        for raw_node in nodes:
            node = _as_dict(raw_node)
            slug = _text(node.get("slug"))
            if not slug:
                continue
            graph_nodes.append(
                EngineWikiGraphNode(
                    slug=slug,
                    title=_text(node.get("title")) or slug,
                    page_type=_text(node.get("page_type")) or "page",
                    link_count=int(node.get("link_count") or 0),
                )
            )
        links: list[tuple[str, str]] = []
        for raw_edge in edges:
            edge = _as_dict(raw_edge)
            source = _text(edge.get("source"))
            target = _text(edge.get("target"))
            if source and target:
                links.append((source, target))
        return EngineWikiGraph(nodes=tuple(graph_nodes), links=tuple(links))

    async def wiki_stats(self, base_id: str) -> EngineWikiStats:
        try:
            row = await self._client.get_wiki_stats(base_id)
        except WeknoraError as error:
            raise KnowledgeEngineError(f"weknora wiki stats failed: {error}") from error
        payload = _as_dict(row)
        pages_by_type = _as_dict(payload.get("pages_by_type"))
        return EngineWikiStats(
            total_pages=int(payload.get("total_pages") or 0),
            pages_by_type={str(key): int(value) for key, value in pages_by_type.items()},
            total_links=int(payload.get("total_links") or 0),
        )


def _wiki_page(row: dict[str, Any]) -> EngineWikiPage | None:
    slug = _text(row.get("slug"))
    if not slug:
        return None
    aliases = row.get("aliases") or ()
    category_path = row.get("category_path") or ()
    return EngineWikiPage(
        slug=slug,
        title=_text(row.get("title")) or slug,
        page_type=_text(row.get("page_type")) or "page",
        content=_text(row.get("content")),
        summary=_text(row.get("summary")),
        aliases=tuple(str(item) for item in aliases if isinstance(item, str)),
        category_path=tuple(str(item) for item in category_path if isinstance(item, str)),
        folder_id=_text(row.get("folder_id")),
    )


def _document_status(row: dict[str, Any]) -> EngineDocumentStatus:
    document_id = _text(row.get("id"))
    if not document_id:
        raise KnowledgeEngineError("weknora document row is missing an id")
    return EngineDocumentStatus(
        document_id=document_id,
        title=_text(row.get("title")) or _text(row.get("file_name")) or document_id,
        parse_status=_text(row.get("parse_status")) or "unknown",
        summary_status=_text(row.get("summary_status")) or "none",
        file_type=_text(row.get("file_type")),
        file_size=int(row.get("file_size") or 0),
        enabled=_text(row.get("enable_status")) != "disabled",
        knowledge_base_id=_text(row.get("knowledge_base_id")),
        created_at=_text(row.get("created_at")) or _text(row.get("updated_at")),
        description=_text(row.get("description")),
    )


def _chunk(row: dict[str, Any]) -> EngineChunk | None:
    chunk_id = _text(row.get("id"))
    if not chunk_id:
        return None
    return EngineChunk(
        chunk_id=chunk_id,
        document_id=_text(row.get("knowledge_id")),
        content=_text(row.get("content")),
        seq=int(row.get("seq_id") or row.get("chunk_index") or 0),
        knowledge_base_id=_text(row.get("knowledge_base_id")),
    )


def _hit(row: dict[str, Any], base_id: str) -> EngineSearchHit:
    chunk_id = _text(row.get("id"))
    if not chunk_id:
        raise KnowledgeEngineError("weknora search hit is missing a chunk id")
    match_type = row.get("match_type")
    return EngineSearchHit(
        chunk_id=chunk_id,
        document_id=_text(row.get("knowledge_id")),
        document_title=_text(row.get("knowledge_title")),
        content=_text(row.get("content")),
        score=float(row.get("score") or 0.0),
        knowledge_base_id=_text(row.get("knowledge_base_id")) or base_id,
        match_type=str(match_type) if match_type is not None else "",
    )
