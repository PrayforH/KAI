"""Engine-agnostic contracts for pluggable knowledge retrieval backends.

The legacy engine stores snapshots and chunks locally; external engines such as
WeKnora keep documents, chunks and indexes inside the remote product and are
reached through HTTP. The port below is the seam that lets ``KnowledgeService``
stay oblivious to which engine backs a given knowledge base.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class KnowledgeEngineError(RuntimeError):
    """Raised when an external knowledge engine call fails."""


class KnowledgeEngineNotConfiguredError(KnowledgeEngineError):
    """Raised when no external knowledge engine is configured."""


@dataclass(frozen=True)
class EngineDocumentStatus:
    """Mirror of a remote document's ingestion state."""

    document_id: str
    title: str
    parse_status: str
    summary_status: str = "none"
    file_type: str = ""
    file_size: int = 0
    enabled: bool = True
    knowledge_base_id: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class EngineChunk:
    """A remote chunk as exposed through citation lookups."""

    chunk_id: str
    document_id: str
    content: str
    seq: int = 0
    knowledge_base_id: str = ""


@dataclass(frozen=True)
class EngineSearchHit:
    """A retrieval hit returned by the remote engine."""

    chunk_id: str
    document_id: str
    document_title: str
    content: str
    score: float
    knowledge_base_id: str
    match_type: str = ""


@dataclass(frozen=True)
class EngineWikiPage:
    """A WeKnora wiki page (summary / entity / concept / index)."""

    slug: str
    title: str
    page_type: str
    content: str = ""
    summary: str = ""
    aliases: tuple[str, ...] = ()
    category_path: tuple[str, ...] = ()
    folder_id: str = ""


@dataclass(frozen=True)
class EngineWikiGraphNode:
    slug: str
    title: str
    page_type: str
    link_count: int = 0


@dataclass(frozen=True)
class EngineWikiGraph:
    nodes: tuple[EngineWikiGraphNode, ...]
    links: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class EngineWikiStats:
    total_pages: int
    pages_by_type: dict[str, int]
    total_links: int


class KnowledgeEnginePort(Protocol):
    """Contract implemented by external knowledge engines (e.g. WeKnora)."""

    async def create_base(
        self,
        *,
        name: str,
        description: str,
        kb_type: str,
    ) -> str:
        """Create a remote knowledge base and return its engine id."""
        ...

    async def delete_base(self, base_id: str) -> None:
        """Delete the remote knowledge base."""
        ...

    async def list_documents(self, base_id: str) -> tuple[EngineDocumentStatus, ...]:
        """List documents with their ingestion status."""
        ...

    async def create_manual_document(
        self,
        base_id: str,
        *,
        title: str,
        content: str,
    ) -> str:
        """Create a manual (inline markdown) document and return its id."""
        ...

    async def upload_document(
        self,
        base_id: str,
        *,
        filename: str,
        content: bytes,
    ) -> str:
        """Upload a file document and return its id."""
        ...

    async def delete_document(self, base_id: str, document_id: str) -> None:
        """Delete a remote document."""
        ...

    async def reparse_document(self, base_id: str, document_id: str) -> None:
        """Schedule a re-parse of a remote document."""
        ...

    async def get_document(self, document_id: str) -> EngineDocumentStatus:
        """Fetch a single document's ingestion status."""
        ...

    async def list_chunks(self, document_id: str) -> tuple[EngineChunk, ...]:
        """List chunks of a remote document."""
        ...

    async def get_chunk(self, chunk_id: str) -> EngineChunk | None:
        """Fetch one chunk by id."""
        ...

    async def search(
        self,
        base_ids: Sequence[str],
        query: str,
        *,
        limit: int,
    ) -> tuple[EngineSearchHit, ...]:
        """Run hybrid retrieval against the given remote knowledge bases."""
        ...

    # --- wiki -------------------------------------------------------------

    async def list_wiki_pages(self, base_id: str) -> tuple[EngineWikiPage, ...]:
        """List wiki pages of a remote knowledge base."""
        ...

    async def get_wiki_page(self, base_id: str, slug: str) -> EngineWikiPage:
        """Fetch a single wiki page by slug."""
        ...

    async def search_wiki_pages(
        self,
        base_id: str,
        query: str,
        *,
        limit: int,
    ) -> tuple[EngineWikiPage, ...]:
        """Full-text search wiki pages by title/content."""
        ...

    async def wiki_graph(self, base_id: str) -> EngineWikiGraph:
        """Fetch the wiki page reference graph."""
        ...

    async def wiki_stats(self, base_id: str) -> EngineWikiStats:
        """Fetch wiki stats (page counts by type, link count)."""
        ...
