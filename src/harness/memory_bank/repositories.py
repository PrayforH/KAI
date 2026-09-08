from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.memory_bank.models import (
    MemoryConsent,
    MemoryEntry,
    MemoryRetention,
    MemorySearchHit,
    MemoryStatus,
)


class MemoryBankRepository(Protocol):
    async def add_entry(self, entry: MemoryEntry) -> None: ...

    async def get_entry(self, tenant_id: str, user_id: str, entry_id: str) -> MemoryEntry: ...

    async def list_entries(
        self,
        tenant_id: str,
        user_id: str,
        *,
        agent_name: str | None,
        statuses: frozenset[MemoryStatus] | None,
        limit: int | None,
        owner_id: str | None = None,
        active_at: datetime | None = None,
    ) -> Sequence[MemoryEntry]: ...

    async def compare_and_set_entry(
        self,
        expected_version: int,
        updated: MemoryEntry,
        *,
        superseded: MemoryEntry | None = None,
        extraction_update: bool = False,
    ) -> bool: ...

    async def list_expired(self, now: datetime, *, limit: int) -> Sequence[MemoryEntry]: ...

    async def get_consent(
        self, tenant_id: str, user_id: str, agent_name: str
    ) -> MemoryConsent | None: ...

    async def put_consent(self, expected_version: int, consent: MemoryConsent) -> bool: ...

    async def get_retention(
        self, tenant_id: str, user_id: str, agent_name: str
    ) -> MemoryRetention | None: ...

    async def put_retention(self, expected_version: int, retention: MemoryRetention) -> bool: ...

    async def put_embedding(
        self, entry: MemoryEntry, model: str, vector: Sequence[float]
    ) -> bool: ...

    async def unindexed(
        self, model: str, now: datetime, *, limit: int = 32
    ) -> Sequence[MemoryEntry]: ...

    async def semantic_search(
        self,
        tenant_id: str,
        user_id: str,
        agent_name: str,
        owner_id: str,
        model: str,
        vector: Sequence[float],
        now: datetime,
        *,
        limit: int,
    ) -> Sequence[MemorySearchHit]: ...


def dedup_key(entry: MemoryEntry) -> str | None:
    if entry.status not in {MemoryStatus.PENDING, MemoryStatus.ACTIVE}:
        return None
    return hashlib.sha256(
        f"{entry.owner_id}\0{entry.agent_name}\0{entry.content_hash}\0{entry.conditions}".encode()
    ).hexdigest()


class InMemoryMemoryBankRepository:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], MemoryEntry] = {}
        self._consents: dict[tuple[str, str, str], MemoryConsent] = {}
        self._retentions: dict[tuple[str, str, str], MemoryRetention] = {}
        self._lock = asyncio.Lock()
        self._vectors: dict[tuple[str, str, str], tuple[int, str, tuple[float, ...]]] = {}

    async def add_entry(self, entry: MemoryEntry) -> None:
        key = (entry.tenant_id, entry.user_id, entry.entry_id)
        async with self._lock:
            if (
                any(
                    e.tenant_id == entry.tenant_id
                    and e.user_id == entry.user_id
                    and dedup_key(entry) is not None
                    and dedup_key(e) == dedup_key(entry)
                    for e in self._entries.values()
                )
                or key in self._entries
            ):
                raise ConflictError(f"memory entry already exists: {entry.entry_id}")
            self._entries[key] = entry

    async def get_entry(self, tenant_id: str, user_id: str, entry_id: str) -> MemoryEntry:
        try:
            return self._entries[(tenant_id, user_id, entry_id)]
        except KeyError as error:
            raise NotFoundError(f"memory entry not found: {entry_id}") from error

    async def list_entries(
        self,
        tenant_id: str,
        user_id: str,
        *,
        agent_name: str | None,
        statuses: frozenset[MemoryStatus] | None,
        limit: int | None,
        owner_id: str | None = None,
        active_at: datetime | None = None,
    ) -> Sequence[MemoryEntry]:
        values = [
            entry
            for (stored_tenant, stored_user, _), entry in self._entries.items()
            if stored_tenant == tenant_id
            and stored_user == user_id
            and (agent_name is None or entry.agent_name == agent_name)
            and (statuses is None or entry.status in statuses)
            and (owner_id is None or entry.owner_id == owner_id)
            and (active_at is None or entry.expires_at is None or entry.expires_at > active_at)
        ]
        return tuple(
            sorted(values, key=lambda item: (item.updated_at, item.entry_id), reverse=True)[:limit]
        )

    async def compare_and_set_entry(
        self,
        expected_version: int,
        updated: MemoryEntry,
        *,
        superseded: MemoryEntry | None = None,
        extraction_update: bool = False,
    ) -> bool:
        if updated.version != expected_version + 1:
            raise ConflictError("memory entry version must increment by one")
        key = (updated.tenant_id, updated.user_id, updated.entry_id)
        async with self._lock:
            current = self._entries.get(key)
            if current is None:
                raise NotFoundError(f"memory entry not found: {updated.entry_id}")
            if current.version != expected_version:
                return False
            if dedup_key(updated) is not None and any(
                e.tenant_id == updated.tenant_id
                and e.user_id == updated.user_id
                and e.entry_id != updated.entry_id
                and (superseded is None or e.entry_id != superseded.entry_id)
                and dedup_key(e) == dedup_key(updated)
                for e in self._entries.values()
            ):
                raise ConflictError("a matching live memory already exists")
            if superseded is not None:
                old_key = (superseded.tenant_id, superseded.user_id, superseded.entry_id)
                old = self._entries.get(old_key)
                if old is None or old.version != superseded.version - 1:
                    return False
                self._entries[old_key] = superseded
                self._vectors.pop(old_key, None)
            self._entries[key] = updated
            self._vectors.pop(key, None)
            return True

    async def list_expired(self, now: datetime, *, limit: int) -> Sequence[MemoryEntry]:
        values = [
            entry
            for entry in self._entries.values()
            if entry.status is MemoryStatus.ACTIVE
            and entry.expires_at is not None
            and entry.expires_at <= now
        ]

        def expiry(item: MemoryEntry) -> datetime:
            assert item.expires_at is not None
            return item.expires_at

        return tuple(sorted(values, key=expiry)[:limit])

    async def get_consent(
        self, tenant_id: str, user_id: str, agent_name: str
    ) -> MemoryConsent | None:
        return self._consents.get((tenant_id, user_id, agent_name))

    async def put_consent(self, expected_version: int, consent: MemoryConsent) -> bool:
        key = (consent.tenant_id, consent.user_id, consent.agent_name)
        async with self._lock:
            current = self._consents.get(key)
            if current is None:
                if expected_version != 0 or consent.version != 1:
                    return False
            elif current.version != expected_version or consent.version != expected_version + 1:
                return False
            self._consents[key] = consent
            return True

    async def get_retention(
        self, tenant_id: str, user_id: str, agent_name: str
    ) -> MemoryRetention | None:
        return self._retentions.get((tenant_id, user_id, agent_name))

    async def put_retention(self, expected_version: int, retention: MemoryRetention) -> bool:
        key = (retention.tenant_id, retention.user_id, retention.agent_name)
        async with self._lock:
            current = self._retentions.get(key)
            if current is None:
                if expected_version != 0 or retention.version != 1:
                    return False
            elif current.version != expected_version or retention.version != expected_version + 1:
                return False
            self._retentions[key] = retention
            return True

    async def put_embedding(self, entry: MemoryEntry, model: str, vector: Sequence[float]) -> bool:
        key = (entry.tenant_id, entry.user_id, entry.entry_id)
        async with self._lock:
            current = self._entries.get(key)
            if (
                current is None
                or current.version != entry.version
                or current.status is not MemoryStatus.ACTIVE
            ):
                return False
            self._vectors[key] = (entry.version, model, tuple(vector))
            return True

    async def unindexed(
        self, model: str, now: datetime, *, limit: int = 32
    ) -> Sequence[MemoryEntry]:
        return tuple(
            e
            for key, e in self._entries.items()
            if e.status is MemoryStatus.ACTIVE
            and (e.expires_at is None or e.expires_at > now)
            and self._vectors.get(key, (0, "", ()))[0:2] != (e.version, model)
        )[:limit]

    async def semantic_search(
        self,
        tenant_id: str,
        user_id: str,
        agent_name: str,
        owner_id: str,
        model: str,
        vector: Sequence[float],
        now: datetime,
        *,
        limit: int,
    ) -> Sequence[MemorySearchHit]:
        entries = await self.list_entries(
            tenant_id,
            user_id,
            agent_name=agent_name,
            statuses=frozenset({MemoryStatus.ACTIVE}),
            limit=None,
            owner_id=owner_id,
            active_at=now,
        )
        hits: list[MemorySearchHit] = []
        for entry in entries:
            stored = self._vectors.get((tenant_id, user_id, entry.entry_id))
            if stored is None or stored[:2] != (entry.version, model):
                continue
            v = stored[2]
            denominator = math.sqrt(sum(x * x for x in v) * sum(x * x for x in vector))
            if denominator:
                score = sum(a * b for a, b in zip(v, vector, strict=True)) / denominator
                hits.append(MemorySearchHit(entry=entry, score=max(0.0, min(1.0, score))))
        return tuple(sorted(hits, key=lambda h: h.score, reverse=True)[:limit])
