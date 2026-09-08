from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from harness.memory_bank.models import MemoryEntry, MemorySearchHit
from harness.memory_bank.safety import safe_terms


class MemorySearchAdapter(Protocol):
    def search(
        self, entries: Sequence[MemoryEntry], query: str, *, limit: int
    ) -> Sequence[MemorySearchHit]: ...


class KeywordMemorySearchAdapter:
    """Deterministic baseline that can be replaced by a tenant-aware vector adapter."""

    def search(
        self, entries: Sequence[MemoryEntry], query: str, *, limit: int
    ) -> Sequence[MemorySearchHit]:
        query_terms = frozenset(safe_terms(query))
        if not query_terms:
            return ()
        hits: list[MemorySearchHit] = []
        for entry in entries:
            entry_terms = frozenset(safe_terms(entry.content))
            matched = tuple(sorted(query_terms.intersection(entry_terms)))
            if not matched:
                continue
            lexical = len(matched) / len(query_terms)
            score = min(1.0, lexical * 0.8 + entry.confidence * 0.2)
            hits.append(MemorySearchHit(entry=entry, score=score, matchedTerms=matched))
        return tuple(
            sorted(
                hits,
                key=lambda item: (item.score, item.entry.updated_at, item.entry.entry_id),
                reverse=True,
            )[:limit]
        )
