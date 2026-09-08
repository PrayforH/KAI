"""WeKnora implementation of the knowledge engine port.

The gateway translates AXIS engine calls into WeKnora REST calls and normalizes
remote payloads (status enums, score scales, chunk shapes) for the service.
"""

from __future__ import annotations

from collections.abc import Sequence

from harness.knowledge.ports import (
    EngineChunk,
    EngineDocumentStatus,
    EngineSearchHit,
    KnowledgeEngineError,
)
from harness.knowledge.weknora.client import WeknoraClient, WeknoraError
from harness.knowledge.weknora.configuration import WeknoraSettings


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


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

    async def create_base(self, *, name: str, description: str, kb_type: str) -> str:
        try:
            payload = await self._client.create_knowledge_base(
                name=name,
                description=description,
                indexing_strategy=self.indexing_strategy(kb_type),
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
        return tuple(_chunk(row) for row in rows)

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


def _document_status(row: dict[str, object]) -> EngineDocumentStatus:
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
    )


def _chunk(row: dict[str, object]) -> EngineChunk | None:
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


def _hit(row: dict[str, object], base_id: str) -> EngineSearchHit:
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
