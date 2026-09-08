from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.knowledge.models import (
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeChunk,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeSyncRun,
)


class KnowledgeRepository(Protocol):
    async def add_base(self, value: KnowledgeBase) -> None: ...

    async def get_base(self, tenant_id: str, reference: str) -> KnowledgeBase: ...

    async def list_bases(self, tenant_id: str) -> Sequence[KnowledgeBase]: ...

    async def compare_and_set_base(self, expected_revision: int, value: KnowledgeBase) -> bool: ...

    async def add_source(self, value: KnowledgeSource) -> None: ...

    async def get_source(self, tenant_id: str, reference: str) -> KnowledgeSource: ...

    async def list_sources(self, tenant_id: str) -> Sequence[KnowledgeSource]: ...

    async def compare_and_set_source(
        self, expected_revision: int, value: KnowledgeSource
    ) -> bool: ...

    async def add_sync(self, value: KnowledgeSyncRun) -> None: ...

    async def put_sync(self, value: KnowledgeSyncRun) -> None: ...

    async def get_sync(self, tenant_id: str, sync_id: str) -> KnowledgeSyncRun: ...

    async def list_syncs(
        self,
        tenant_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSyncRun]: ...

    async def publish_snapshot(
        self,
        *,
        expected_source_revision: int,
        source: KnowledgeSource,
        snapshot: KnowledgeSnapshot,
        chunks: Sequence[KnowledgeChunk],
        sync: KnowledgeSyncRun,
    ) -> bool: ...

    async def get_snapshot(self, tenant_id: str, snapshot_id: str) -> KnowledgeSnapshot: ...

    async def list_snapshots(
        self,
        tenant_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSnapshot]: ...

    async def list_chunks(
        self,
        tenant_id: str,
        snapshot_ids: frozenset[str],
    ) -> Sequence[KnowledgeChunk]: ...

    async def add_member(self, value: KnowledgeBaseMember) -> None: ...

    async def get_member(self, tenant_id: str, member_id: str) -> KnowledgeBaseMember: ...

    async def list_members(
        self,
        tenant_id: str,
        *,
        knowledge_base_reference: str | None = None,
    ) -> Sequence[KnowledgeBaseMember]: ...

    async def put_member(self, value: KnowledgeBaseMember) -> None: ...

    async def delete_member(self, tenant_id: str, member_id: str) -> bool: ...


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self._bases: dict[tuple[str, str], KnowledgeBase] = {}
        self._sources: dict[tuple[str, str], KnowledgeSource] = {}
        self._syncs: dict[tuple[str, str], KnowledgeSyncRun] = {}
        self._snapshots: dict[tuple[str, str], KnowledgeSnapshot] = {}
        self._chunks: dict[tuple[str, str, str], KnowledgeChunk] = {}
        self._members: dict[tuple[str, str], KnowledgeBaseMember] = {}
        self._lock = asyncio.Lock()

    async def add_base(self, value: KnowledgeBase) -> None:
        key = (value.tenant_id, value.reference)
        async with self._lock:
            if key in self._bases:
                raise ConflictError(f"Knowledge Base already exists: {value.reference}")
            self._bases[key] = value

    async def get_base(self, tenant_id: str, reference: str) -> KnowledgeBase:
        try:
            return self._bases[(tenant_id, reference)]
        except KeyError as error:
            raise NotFoundError(f"Knowledge Base not found: {reference}") from error

    async def list_bases(self, tenant_id: str) -> Sequence[KnowledgeBase]:
        values = [
            item for (stored_tenant, _), item in self._bases.items() if stored_tenant == tenant_id
        ]
        return tuple(sorted(values, key=lambda item: item.reference))

    async def compare_and_set_base(self, expected_revision: int, value: KnowledgeBase) -> bool:
        if value.revision != expected_revision + 1:
            raise ConflictError("Knowledge Base revision must increment by one")
        key = (value.tenant_id, value.reference)
        async with self._lock:
            current = self._bases.get(key)
            if current is None:
                raise NotFoundError(f"Knowledge Base not found: {value.reference}")
            if current.revision != expected_revision:
                return False
            self._bases[key] = value
            return True

    async def add_source(self, value: KnowledgeSource) -> None:
        key = (value.tenant_id, value.reference)
        async with self._lock:
            if key in self._sources:
                raise ConflictError(f"knowledge source already exists: {value.reference}")
            self._sources[key] = value

    async def get_source(self, tenant_id: str, reference: str) -> KnowledgeSource:
        try:
            return self._sources[(tenant_id, reference)]
        except KeyError as error:
            raise NotFoundError(f"knowledge source not found: {reference}") from error

    async def list_sources(self, tenant_id: str) -> Sequence[KnowledgeSource]:
        values = [
            item for (stored_tenant, _), item in self._sources.items() if stored_tenant == tenant_id
        ]
        return tuple(sorted(values, key=lambda item: item.reference))

    async def compare_and_set_source(self, expected_revision: int, value: KnowledgeSource) -> bool:
        if value.revision != expected_revision + 1:
            raise ConflictError("knowledge source revision must increment by one")
        key = (value.tenant_id, value.reference)
        async with self._lock:
            current = self._sources.get(key)
            if current is None:
                raise NotFoundError(f"knowledge source not found: {value.reference}")
            if current.revision != expected_revision:
                return False
            self._sources[key] = value
            return True

    async def add_sync(self, value: KnowledgeSyncRun) -> None:
        key = (value.tenant_id, value.sync_id)
        async with self._lock:
            if key in self._syncs:
                raise ConflictError(f"knowledge sync already exists: {value.sync_id}")
            self._syncs[key] = value

    async def put_sync(self, value: KnowledgeSyncRun) -> None:
        key = (value.tenant_id, value.sync_id)
        async with self._lock:
            if key not in self._syncs:
                raise NotFoundError(f"knowledge sync not found: {value.sync_id}")
            self._syncs[key] = value

    async def get_sync(self, tenant_id: str, sync_id: str) -> KnowledgeSyncRun:
        try:
            return self._syncs[(tenant_id, sync_id)]
        except KeyError as error:
            raise NotFoundError(f"knowledge sync not found: {sync_id}") from error

    async def list_syncs(
        self,
        tenant_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSyncRun]:
        values = [
            item
            for (stored_tenant, _), item in self._syncs.items()
            if stored_tenant == tenant_id
            and (source_reference is None or item.source_reference == source_reference)
        ]
        return tuple(
            sorted(
                values,
                key=lambda item: (item.created_at, item.sync_id),
                reverse=True,
            )[:limit]
        )

    async def publish_snapshot(
        self,
        *,
        expected_source_revision: int,
        source: KnowledgeSource,
        snapshot: KnowledgeSnapshot,
        chunks: Sequence[KnowledgeChunk],
        sync: KnowledgeSyncRun,
    ) -> bool:
        if source.revision != expected_source_revision + 1:
            raise ConflictError("knowledge source revision must increment by one")
        source_key = (source.tenant_id, source.reference)
        snapshot_key = (snapshot.tenant_id, snapshot.snapshot_id)
        async with self._lock:
            current = self._sources.get(source_key)
            if current is None:
                raise NotFoundError(f"knowledge source not found: {source.reference}")
            if current.revision != expected_source_revision:
                return False
            if snapshot_key in self._snapshots:
                raise ConflictError(f"knowledge snapshot already exists: {snapshot.snapshot_id}")
            self._snapshots[snapshot_key] = snapshot
            for chunk in chunks:
                self._chunks[(chunk.tenant_id, chunk.snapshot_id, chunk.chunk_id)] = chunk
            self._sources[source_key] = source
            self._syncs[(sync.tenant_id, sync.sync_id)] = sync
            return True

    async def get_snapshot(self, tenant_id: str, snapshot_id: str) -> KnowledgeSnapshot:
        try:
            return self._snapshots[(tenant_id, snapshot_id)]
        except KeyError as error:
            raise NotFoundError(f"knowledge snapshot not found: {snapshot_id}") from error

    async def list_snapshots(
        self,
        tenant_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSnapshot]:
        values = [
            item
            for (stored_tenant, _), item in self._snapshots.items()
            if stored_tenant == tenant_id
            and (source_reference is None or item.source_reference == source_reference)
        ]
        return tuple(
            sorted(
                values,
                key=lambda item: (item.created_at, item.snapshot_id),
                reverse=True,
            )[:limit]
        )

    async def list_chunks(
        self,
        tenant_id: str,
        snapshot_ids: frozenset[str],
    ) -> Sequence[KnowledgeChunk]:
        if not snapshot_ids:
            return ()
        values = [
            item
            for (stored_tenant, snapshot_id, _), item in self._chunks.items()
            if stored_tenant == tenant_id and snapshot_id in snapshot_ids
        ]
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    item.snapshot_id,
                    item.document_id,
                    item.ordinal,
                ),
            )
        )

    async def add_member(self, value: KnowledgeBaseMember) -> None:
        key = (value.tenant_id, value.member_id)
        if key in self._members:
            raise ConflictError(f"knowledge base member already exists: {value.member_id}")
        duplicate = [
            item
            for item in self._members.values()
            if item.tenant_id == value.tenant_id
            and item.knowledge_base_reference == value.knowledge_base_reference
            and item.subject_type is value.subject_type
            and item.subject_id == value.subject_id
        ]
        if duplicate:
            raise ConflictError("knowledge base member subject already granted")
        self._members[key] = value

    async def get_member(self, tenant_id: str, member_id: str) -> KnowledgeBaseMember:
        try:
            return self._members[(tenant_id, member_id)]
        except KeyError as error:
            raise NotFoundError(f"knowledge base member not found: {member_id}") from error

    async def list_members(
        self,
        tenant_id: str,
        *,
        knowledge_base_reference: str | None = None,
    ) -> Sequence[KnowledgeBaseMember]:
        values = [
            item
            for (item_tenant, _), item in sorted(self._members.items())
            if item_tenant == tenant_id
            and (
                knowledge_base_reference is None
                or item.knowledge_base_reference == knowledge_base_reference
            )
        ]
        return tuple(values)

    async def put_member(self, value: KnowledgeBaseMember) -> None:
        key = (value.tenant_id, value.member_id)
        if key not in self._members:
            raise NotFoundError(f"knowledge base member not found: {value.member_id}")
        self._members[key] = value

    async def delete_member(self, tenant_id: str, member_id: str) -> bool:
        return self._members.pop((tenant_id, member_id), None) is not None
