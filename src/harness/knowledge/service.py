from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from harness.auth.audit import AuditService
from harness.core.errors import ConflictError, NotFoundError
from harness.knowledge.connectors import (
    KnowledgeConnectorError,
    KnowledgeConnectorRegistry,
)
from harness.knowledge.directory import IdentityDirectoryPort
from harness.knowledge.models import (
    AddKnowledgeMembersRequest,
    AddKnowledgeMembersResult,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeSourceRequest,
    KnowledgeAcl,
    KnowledgeBase,
    KnowledgeBaseEngine,
    KnowledgeBaseMember,
    KnowledgeChunk,
    KnowledgeCitation,
    KnowledgeDocumentChunk,
    KnowledgeDocumentStatus,
    KnowledgeDocumentTable,
    KnowledgeMemberRole,
    KnowledgeMemberSubject,
    KnowledgeSearchHit,
    KnowledgeSnapshot,
    KnowledgeSnapshotBinding,
    KnowledgeSource,
    KnowledgeSourceHealth,
    KnowledgeSourceKind,
    KnowledgeSyncRun,
    KnowledgeSyncStatus,
    KnowledgeVisibility,
    KnowledgeWikiGraph,
    KnowledgeWikiGraphNode,
    KnowledgeWikiPage,
    KnowledgeWikiStats,
    ReplaceKnowledgeBaseRequest,
    ReplaceKnowledgeSourceRequest,
    SearchKnowledgeResponse,
    WeknoraKnowledgeConfig,
)
from harness.knowledge.ports import (
    KnowledgeEngineError,
    KnowledgeEngineNotConfiguredError,
    KnowledgeEnginePort,
)
from harness.knowledge.repositories import KnowledgeRepository
from harness.knowledge.search import HybridKnowledgeSearch, tokenize
from harness.knowledge.spreadsheet import is_spreadsheet, parse_spreadsheet

TeamGrantChecker = Callable[[str, str, tuple[str, ...], str], Awaitable[bool]]


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        connectors: KnowledgeConnectorRegistry | None = None,
        search: HybridKnowledgeSearch | None = None,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
        chunk_characters: int = 1_600,
        chunk_overlap: int = 240,
        team_grant_checker: TeamGrantChecker | None = None,
        engine: KnowledgeEnginePort | None = None,
        directory: IdentityDirectoryPort | None = None,
    ) -> None:
        if chunk_overlap >= chunk_characters:
            raise ValueError("knowledge chunk overlap must be smaller than chunk size")
        self.repository = repository
        self._connectors = connectors or KnowledgeConnectorRegistry()
        self._search = search or HybridKnowledgeSearch()
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = id_generator or _id
        self._chunk_characters = chunk_characters
        self._chunk_overlap = chunk_overlap
        self._team_grant_checker = team_grant_checker
        self._engine = engine
        self._directory = directory

    def configure_team_grant_checker(self, checker: TeamGrantChecker) -> None:
        if self._team_grant_checker is not None:
            raise RuntimeError("team knowledge grant checker is already configured")
        self._team_grant_checker = checker

    async def create_base(
        self,
        tenant_id: str,
        actor_id: str,
        request: CreateKnowledgeBaseRequest,
    ) -> KnowledgeBase:
        await self._require_sources(
            tenant_id,
            request.source_references,
            owner_user_id=actor_id,
        )
        now = self._clock()
        engine_ref = ""
        source_references = tuple(request.source_references)
        if request.engine is KnowledgeBaseEngine.WEKNORA:
            engine_ref = await self._engine_create_base(
                request.display_name,
                request.description,
                request.kb_type.value,
            )
            # Engine-backed bases get one 1:1 link source so the existing
            # ACL, sync-mirror and proxy paths keep operating per source.
            link_reference = request.reference
            await self.repository.add_source(
                KnowledgeSource(
                    tenantId=tenant_id,
                    reference=link_reference,
                    displayName=request.display_name,
                    description=request.description,
                    kind=KnowledgeSourceKind.WEKNORA,
                    config=WeknoraKnowledgeConfig(weknoraBaseId=engine_ref),
                    acl=self._personal_acl(actor_id, KnowledgeAcl()),
                    revision=1,
                    health=KnowledgeSourceHealth.PENDING,
                    createdBy=actor_id,
                    updatedBy=actor_id,
                    createdAt=now,
                    updatedAt=now,
                )
            )
            source_references = (link_reference,)
        value = KnowledgeBase(
            tenantId=tenant_id,
            reference=request.reference,
            displayName=request.display_name,
            description=request.description,
            sourceReferences=source_references,
            kbType=request.kb_type,
            engine=request.engine,
            engineRef=engine_ref,
            revision=1,
            createdBy=actor_id,
            updatedBy=actor_id,
            createdAt=now,
            updatedAt=now,
        )
        await self.repository.add_base(value)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.base.create",
            value.reference,
            {
                "source_count": len(value.source_references),
                "engine": value.engine.value,
                "kb_type": value.kb_type.value,
            },
        )
        if engine_ref:
            # Mirror remote ingestion state so the link source becomes usable
            # (and the console sees documents) right after creation.
            await self.sync_source(tenant_id, actor_id, request.reference)
        return value

    async def replace_base(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        request: ReplaceKnowledgeBaseRequest,
    ) -> KnowledgeBase:
        current = await self._get_owned_base(tenant_id, actor_id, reference)
        if current.revision != request.expected_revision:
            raise ConflictError("Knowledge Base revision changed")
        await self._require_sources(
            tenant_id,
            request.source_references,
            owner_user_id=actor_id,
        )
        updated = current.model_copy(
            update={
                "display_name": request.display_name,
                "description": request.description,
                "source_references": request.source_references,
                "revision": current.revision + 1,
                "updated_by": actor_id,
                "updated_at": self._clock(),
            }
        )
        if not await self.repository.compare_and_set_base(current.revision, updated):
            raise ConflictError("Knowledge Base changed while it was updated")
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.base.update",
            reference,
            {"source_count": len(updated.source_references)},
        )
        return updated

    async def list_bases(
        self,
        tenant_id: str,
        owner_user_id: str | None = None,
    ) -> Sequence[KnowledgeBase]:
        values = await self.repository.list_bases(tenant_id)
        # Refresh engine-backed counts concurrently; the list is small and a
        # stale badge is worse than a couple of extra remote reads.
        values = tuple(
            await asyncio.gather(
                *(self._with_document_count(tenant_id, item) for item in values)
            )
        )
        if owner_user_id is None:
            return values
        member_references = {
            item.knowledge_base_reference
            for item in await self.repository.list_members(tenant_id)
            if item.subject_type is KnowledgeMemberSubject.USER and item.subject_id == owner_user_id
        }
        return tuple(
            item
            for item in values
            if item.created_by == owner_user_id or item.reference in member_references
        )

    async def get_base(
        self,
        tenant_id: str,
        reference: str,
        owner_user_id: str | None = None,
    ) -> KnowledgeBase:
        if owner_user_id is None:
            return await self.repository.get_base(tenant_id, reference)
        return await self._get_owned_base(tenant_id, owner_user_id, reference)

    async def create_source(
        self,
        tenant_id: str,
        actor_id: str,
        request: CreateKnowledgeSourceRequest,
    ) -> tuple[KnowledgeSource, KnowledgeSyncRun | None]:
        now = self._clock()
        value = KnowledgeSource(
            tenantId=tenant_id,
            reference=request.reference,
            displayName=request.display_name,
            description=request.description,
            kind=request.kind,
            config=request.config,
            acl=self._personal_acl(actor_id, request.acl),
            revision=1,
            health=KnowledgeSourceHealth.PENDING,
            createdBy=actor_id,
            updatedBy=actor_id,
            createdAt=now,
            updatedAt=now,
        )
        await self.repository.add_source(value)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.source.create",
            value.reference,
            {"kind": value.kind.value, "visibility": value.acl.visibility.value},
        )
        if not request.sync_now:
            return value, None
        sync = await self.sync_source(tenant_id, actor_id, value.reference)
        return await self.repository.get_source(tenant_id, value.reference), sync

    async def replace_source(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        request: ReplaceKnowledgeSourceRequest,
    ) -> KnowledgeSource:
        current = await self._get_owned_source(tenant_id, actor_id, reference)
        if current.revision != request.expected_revision:
            raise ConflictError("knowledge source revision changed")
        if request.config.type != current.kind.value:
            raise ConflictError("knowledge source connector kind cannot be changed")
        updated = current.model_copy(
            update={
                "display_name": request.display_name,
                "description": request.description,
                "config": request.config,
                "acl": self._personal_acl(actor_id, request.acl),
                "health": (
                    KnowledgeSourceHealth.PENDING
                    if request.enabled
                    else KnowledgeSourceHealth.DISABLED
                ),
                "revision": current.revision + 1,
                "updated_by": actor_id,
                "updated_at": self._clock(),
                "last_error": None,
            }
        )
        if not await self.repository.compare_and_set_source(current.revision, updated):
            raise ConflictError("knowledge source changed while it was updated")
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.source.update",
            reference,
            {"enabled": request.enabled},
        )
        return updated

    async def list_sources(
        self,
        tenant_id: str,
        owner_user_id: str | None = None,
    ) -> Sequence[KnowledgeSource]:
        values = await self.repository.list_sources(tenant_id)
        if owner_user_id is None:
            return values
        return tuple(item for item in values if item.created_by == owner_user_id)

    async def get_source(
        self,
        tenant_id: str,
        reference: str,
        owner_user_id: str | None = None,
    ) -> KnowledgeSource:
        if owner_user_id is None:
            return await self.repository.get_source(tenant_id, reference)
        return await self._get_owned_source(tenant_id, owner_user_id, reference)

    async def list_syncs(
        self,
        tenant_id: str,
        owner_user_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSyncRun]:
        owned_references = {
            item.reference for item in await self.list_sources(tenant_id, owner_user_id)
        }
        if source_reference is not None and source_reference not in owned_references:
            return ()
        values = await self.repository.list_syncs(
            tenant_id,
            source_reference=source_reference,
            limit=max(limit, 10_000),
        )
        return tuple(item for item in values if item.source_reference in owned_references)[:limit]

    async def list_snapshots(
        self,
        tenant_id: str,
        owner_user_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSnapshot]:
        owned_references = {
            item.reference for item in await self.list_sources(tenant_id, owner_user_id)
        }
        if source_reference is not None and source_reference not in owned_references:
            return ()
        values = await self.repository.list_snapshots(
            tenant_id,
            source_reference=source_reference,
            limit=max(limit, 10_000),
        )
        return tuple(item for item in values if item.source_reference in owned_references)[:limit]

    async def sync_source(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> KnowledgeSyncRun:
        source = await self._get_owned_source(tenant_id, actor_id, reference)
        if source.health is KnowledgeSourceHealth.DISABLED:
            raise ConflictError("disabled knowledge source cannot be synchronized")
        now = self._clock()
        sync = KnowledgeSyncRun(
            tenantId=tenant_id,
            syncId=self._ids("knowledge_sync"),
            sourceReference=reference,
            sourceRevision=source.revision,
            status=KnowledgeSyncStatus.RUNNING,
            checkpointBefore=source.checkpoint,
            createdBy=actor_id,
            createdAt=now,
            startedAt=now,
        )
        await self.repository.add_sync(sync)
        if source.kind.value == "weknora":
            return await self._sync_weknora_source(source, sync)
        try:
            result = await self._connectors.resolve(source.kind).sync(
                source.config,
                source.checkpoint,
            )
            if not result.documents:
                raise KnowledgeConnectorError("connector returned no documents")
            if source.active_snapshot_id is not None and result.checkpoint.get(
                "contentHash"
            ) == source.checkpoint.get("contentHash"):
                completed_at = self._clock()
                updated_source = source.model_copy(
                    update={
                        "revision": source.revision + 1,
                        "health": KnowledgeSourceHealth.HEALTHY,
                        "last_sync_id": sync.sync_id,
                        "last_sync_at": completed_at,
                        "last_error": None,
                        "updated_by": actor_id,
                        "updated_at": completed_at,
                    }
                )
                if not await self.repository.compare_and_set_source(
                    source.revision, updated_source
                ):
                    raise ConflictError("knowledge source changed while sync completed")
                completed = sync.model_copy(
                    update={
                        "status": KnowledgeSyncStatus.UNCHANGED,
                        "checkpoint_after": result.checkpoint,
                        "snapshot_id": source.active_snapshot_id,
                        "documents_seen": len(result.documents),
                        "completed_at": completed_at,
                    }
                )
                await self.repository.put_sync(completed)
                return completed

            snapshot_id = self._ids("knowledge_snapshot")
            chunks = self._chunk_documents(
                tenant_id=tenant_id,
                source=source,
                snapshot_id=snapshot_id,
                documents=result.documents,
            )
            if not chunks:
                raise KnowledgeConnectorError("connector documents produced no chunks")
            completed_at = self._clock()
            snapshot = KnowledgeSnapshot(
                tenantId=tenant_id,
                snapshotId=snapshot_id,
                sourceReference=source.reference,
                sourceRevision=source.revision + 1,
                contentHash=KnowledgeSnapshot.digest_chunks(chunks),
                documentCount=len(result.documents),
                chunkCount=len(chunks),
                checkpoint=result.checkpoint,
                createdAt=completed_at,
            )
            completed = sync.model_copy(
                update={
                    "status": KnowledgeSyncStatus.SUCCEEDED,
                    "checkpoint_after": result.checkpoint,
                    "snapshot_id": snapshot_id,
                    "documents_seen": len(result.documents),
                    "chunks_written": len(chunks),
                    "completed_at": completed_at,
                }
            )
            updated_source = source.model_copy(
                update={
                    "revision": source.revision + 1,
                    "health": KnowledgeSourceHealth.HEALTHY,
                    "active_snapshot_id": snapshot_id,
                    "checkpoint": result.checkpoint,
                    "last_sync_id": sync.sync_id,
                    "last_sync_at": completed_at,
                    "last_error": None,
                    "updated_by": actor_id,
                    "updated_at": completed_at,
                }
            )
            if not await self.repository.publish_snapshot(
                expected_source_revision=source.revision,
                source=updated_source,
                snapshot=snapshot,
                chunks=chunks,
                sync=completed,
            ):
                raise ConflictError("knowledge source changed while snapshot was published")
            await self._record(
                tenant_id,
                actor_id,
                "knowledge.source.sync",
                source.reference,
                {
                    "sync_id": sync.sync_id,
                    "snapshot_id": snapshot_id,
                    "documents": len(result.documents),
                    "chunks": len(chunks),
                },
            )
            return completed
        except Exception as error:
            completed_at = self._clock()
            failed = sync.model_copy(
                update={
                    "status": KnowledgeSyncStatus.FAILED,
                    "error_code": type(error).__name__,
                    "error_message": str(error)[:1_000],
                    "completed_at": completed_at,
                }
            )
            await self.repository.put_sync(failed)
            # A concurrent edit or sync conflict is not connector degradation. More
            # importantly, an older failed worker must never overwrite a newer source
            # revision or replace its healthy active snapshot.
            if not isinstance(error, ConflictError):
                degraded = source.model_copy(
                    update={
                        "revision": source.revision + 1,
                        "health": KnowledgeSourceHealth.DEGRADED,
                        "last_sync_id": sync.sync_id,
                        "last_sync_at": completed_at,
                        "last_error": str(error)[:1_000],
                        "updated_by": actor_id,
                        "updated_at": completed_at,
                    }
                )
                await self.repository.compare_and_set_source(
                    source.revision,
                    degraded,
                )
            await self._record(
                tenant_id,
                actor_id,
                "knowledge.source.sync",
                source.reference,
                {
                    "sync_id": sync.sync_id,
                    "outcome": "failed",
                    "error_code": type(error).__name__,
                },
            )
            if isinstance(error, (ConflictError, KnowledgeConnectorError)):
                raise
            raise KnowledgeConnectorError("knowledge source synchronization failed") from error

    async def resolve_bindings(
        self,
        tenant_id: str,
        actor_id: str,
        knowledge_base_references: Sequence[str],
        team_ids: tuple[str, ...] = (),
    ) -> tuple[KnowledgeSnapshotBinding, ...]:
        bindings: list[KnowledgeSnapshotBinding] = []
        seen: set[tuple[str, str]] = set()
        for base_reference in knowledge_base_references:
            base = await self.repository.get_base(tenant_id, base_reference)
            if not await self._allows_base(
                tenant_id,
                actor_id,
                team_ids,
                base,
            ):
                continue
            for source_reference in base.source_references:
                source = await self.repository.get_source(tenant_id, source_reference)
                key = (base_reference, source_reference)
                if (
                    key in seen
                    or not await self._allows_source(
                        tenant_id, actor_id, team_ids, base_reference, source
                    )
                    or source.health is not KnowledgeSourceHealth.HEALTHY
                ):
                    continue
                if source.kind.value == "weknora":
                    # Engine-backed sources hold no local snapshot; retrieval is
                    # delegated to the external engine at search time.
                    seen.add(key)
                    bindings.append(
                        KnowledgeSnapshotBinding(
                            knowledgeBaseReference=base_reference,
                            sourceReference=source_reference,
                            snapshotId=f"weknora:{source_reference}",
                            trust=source.result_trust,
                        )
                    )
                    continue
                if source.active_snapshot_id is None:
                    continue
                seen.add(key)
                bindings.append(
                    KnowledgeSnapshotBinding(
                        knowledgeBaseReference=base_reference,
                        sourceReference=source_reference,
                        snapshotId=source.active_snapshot_id,
                        trust=source.result_trust,
                    )
                )
        return tuple(bindings)

    async def search(
        self,
        tenant_id: str,
        actor_id: str,
        query: str,
        *,
        knowledge_base_references: Sequence[str] = (),
        bindings: Sequence[KnowledgeSnapshotBinding] = (),
        limit: int = 8,
        team_ids: tuple[str, ...] = (),
    ) -> SearchKnowledgeResponse:
        resolved_bindings = (
            tuple(bindings)
            if bindings
            else await self.resolve_bindings(
                tenant_id,
                actor_id,
                knowledge_base_references,
                team_ids,
            )
        )
        # Recheck ACL before loading any candidate text. Session-pinned snapshot IDs do not
        # bypass later access revocation.
        allowed: list[KnowledgeSnapshotBinding] = []
        source_by_reference: dict[str, KnowledgeSource] = {}
        for binding in resolved_bindings:
            source = source_by_reference.get(binding.source_reference)
            if source is None:
                source = await self.repository.get_source(tenant_id, binding.source_reference)
                source_by_reference[source.reference] = source
            if await self._allows_source(
                tenant_id,
                actor_id,
                team_ids,
                binding.knowledge_base_reference,
                source,
            ):
                allowed.append(binding)
        snapshots = frozenset(
            item.snapshot_id for item in allowed if not item.snapshot_id.startswith("weknora:")
        )
        chunks = await self.repository.list_chunks(tenant_id, snapshots)
        ranked = self._search.search(chunks, query, limit=limit)
        binding_by_pair = {(item.source_reference, item.snapshot_id): item for item in allowed}
        hits: list[KnowledgeSearchHit] = []
        for item in ranked:
            chunk = item.chunk
            binding = binding_by_pair[(chunk.source_reference, chunk.snapshot_id)]
            source = source_by_reference[chunk.source_reference]
            hits.append(
                KnowledgeSearchHit(
                    content=chunk.content,
                    score=item.score,
                    trust=binding.trust,
                    citation=KnowledgeCitation(
                        knowledgeBaseReference=(binding.knowledge_base_reference),
                        sourceReference=chunk.source_reference,
                        sourceDisplayName=source.display_name,
                        snapshotId=chunk.snapshot_id,
                        documentId=chunk.document_id,
                        chunkId=chunk.chunk_id,
                        title=chunk.title,
                        uri=chunk.source_uri,
                    ),
                    matchedTerms=item.matched_terms,
                )
            )
        hits.extend(
            await self._engine_search_hits(tenant_id, allowed, source_by_reference, query, limit)
        )
        hits.sort(key=lambda item: item.score, reverse=True)
        hits = hits[:limit]
        return SearchKnowledgeResponse(
            hits=tuple(hits),
            searchedSnapshotIds=tuple(sorted(snapshots)),
        )

    async def _engine_search_hits(
        self,
        tenant_id: str,
        allowed: Sequence[KnowledgeSnapshotBinding],
        source_by_reference: dict[str, KnowledgeSource],
        query: str,
        limit: int,
    ) -> list[KnowledgeSearchHit]:
        engine_bindings = [item for item in allowed if item.snapshot_id.startswith("weknora:")]
        if not engine_bindings:
            return []
        if self._engine is None:
            raise KnowledgeEngineError("weknora knowledge engine is not configured")
        base_ids: list[str] = []
        base_by_id: dict[str, KnowledgeSnapshotBinding] = {}
        for binding in engine_bindings:
            source = source_by_reference[binding.source_reference]
            config = source.config
            remote_id = getattr(config, "weknora_base_id", "")
            if not remote_id or remote_id in base_by_id:
                continue
            base_ids.append(remote_id)
            base_by_id[remote_id] = binding
        engine_hits = await self._engine.search(base_ids, query, limit=limit)
        top = max((item.score for item in engine_hits), default=0.0)
        hits: list[KnowledgeSearchHit] = []
        for item in engine_hits:
            binding = base_by_id.get(item.knowledge_base_id)
            if binding is None:
                continue
            source = source_by_reference[binding.source_reference]
            normalized = item.score / top if top > 0 else 0.0
            hits.append(
                KnowledgeSearchHit(
                    content=item.content,
                    score=normalized,
                    trust=binding.trust,
                    citation=KnowledgeCitation(
                        knowledgeBaseReference=binding.knowledge_base_reference,
                        sourceReference=binding.source_reference,
                        sourceDisplayName=source.display_name,
                        snapshotId=binding.snapshot_id,
                        documentId=item.document_id,
                        chunkId=item.chunk_id,
                        title=item.document_title,
                        uri="",
                    ),
                    matchedTerms=(),
                )
            )
        return hits

    async def _allows_source(
        self,
        tenant_id: str,
        actor_id: str,
        team_ids: tuple[str, ...],
        base_reference: str,
        source: KnowledgeSource,
    ) -> bool:
        if source.created_by == actor_id:
            return True
        if source.acl.visibility is KnowledgeVisibility.RESTRICTED and source.acl.allows(actor_id):
            return True
        if await self._member_role(tenant_id, actor_id, base_reference) is not None:
            return True
        return bool(
            team_ids
            and self._team_grant_checker is not None
            and await self._team_grant_checker(tenant_id, actor_id, team_ids, base_reference)
        )

    async def _allows_base(
        self,
        tenant_id: str,
        actor_id: str,
        team_ids: tuple[str, ...],
        base: KnowledgeBase,
    ) -> bool:
        if base.created_by == actor_id:
            return True
        if await self._member_role(tenant_id, actor_id, base.reference) is not None:
            return True
        return bool(
            team_ids
            and self._team_grant_checker is not None
            and await self._team_grant_checker(
                tenant_id,
                actor_id,
                team_ids,
                base.reference,
            )
        )

    async def _require_editor(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> None:
        """Writes require the creator or an editor grant; viewers are rejected."""
        try:
            base = await self.repository.get_base(tenant_id, reference)
        except NotFoundError:
            # Standalone engine source without a owning base record.
            source = await self.repository.get_source(tenant_id, reference)
            if source.created_by != actor_id:
                raise NotFoundError(f"knowledge source not found: {reference}") from None
            return
        if base.created_by == actor_id:
            return
        role = await self._member_role(tenant_id, actor_id, reference)
        if role is None or role.rank < KnowledgeMemberRole.EDITOR.rank:
            raise NotFoundError(f"Knowledge Base not found: {reference}")

    async def get_visible_chunk(
        self,
        tenant_id: str,
        actor_id: str,
        snapshot_id: str,
        chunk_id: str,
    ) -> KnowledgeChunk:
        snapshot = await self.repository.get_snapshot(tenant_id, snapshot_id)
        source = await self.repository.get_source(
            tenant_id,
            snapshot.source_reference,
        )
        if source.created_by != actor_id and not (
            source.acl.visibility is KnowledgeVisibility.RESTRICTED and source.acl.allows(actor_id)
        ):
            raise NotFoundError("knowledge citation not found")
        chunks = await self.repository.list_chunks(
            tenant_id,
            frozenset({snapshot_id}),
        )
        chunk = next(
            (item for item in chunks if item.chunk_id == chunk_id),
            None,
        )
        if chunk is None:
            raise NotFoundError("knowledge citation not found")
        return chunk

    async def require_bases(
        self,
        tenant_id: str,
        references: Sequence[str],
    ) -> None:
        for reference in references:
            await self.repository.get_base(tenant_id, reference)

    # --- engine-backed (WeKnora) knowledge bases --------------------------

    async def _refresh_document_count(
        self,
        tenant_id: str,
        reference: str,
        source: KnowledgeSource,
    ) -> None:
        """Persist the live document count so the console card stays accurate.

        WeKnora's document list is the source of truth; the sync checkpoint is
        only a cache, so a create/delete must refresh it instead of waiting for
        the next manual sync.
        """
        remote_id = getattr(source.config, "weknora_base_id", "")
        if not remote_id:
            return
        documents = await self._require_engine().list_documents(remote_id)
        checkpoint = dict(source.checkpoint)
        checkpoint["documents"] = len(documents)
        if checkpoint == source.checkpoint:
            return
        updated = source.model_copy(
            update={
                "revision": source.revision + 1,
                "checkpoint": checkpoint,
                "updated_at": self._clock(),
            }
        )
        await self.repository.compare_and_set_source(source.revision, updated)

    async def _with_document_count(
        self,
        tenant_id: str,
        base: KnowledgeBase,
    ) -> KnowledgeBase:
        """Attach the engine's live document count.

        Documents can be added directly in WeKnora, so the cached checkpoint is
        only a fallback when the remote read fails.
        """
        if base.engine is not KnowledgeBaseEngine.WEKNORA or not base.engine_ref:
            return base
        try:
            source = await self.repository.get_source(tenant_id, base.reference)
        except NotFoundError:
            return base
        cached = source.checkpoint.get("documents")
        fallback = cached if isinstance(cached, int) and cached >= 0 else 0
        remote_id = getattr(source.config, "weknora_base_id", "")
        if self._engine is None or not remote_id:
            return base.model_copy(update={"document_count": fallback})
        try:
            documents = await self._engine.list_documents(remote_id)
        except KnowledgeEngineError:
            return base.model_copy(update={"document_count": fallback})
        count = len(documents)
        if count != fallback:
            await self._refresh_document_count(tenant_id, base.reference, source)
        return base.model_copy(update={"document_count": count})

    def _require_engine(self) -> KnowledgeEnginePort:
        if self._engine is None:
            raise KnowledgeEngineNotConfiguredError("weknora knowledge engine is not configured")
        return self._engine

    async def _engine_create_base(self, name: str, description: str, kb_type: str) -> str:
        return await self._require_engine().create_base(
            name=name,
            description=description,
            kb_type=kb_type,
        )

    async def _sync_weknora_source(
        self,
        source: KnowledgeSource,
        sync: KnowledgeSyncRun,
    ) -> KnowledgeSyncRun:
        """Mirror remote ingestion status into a sync run; chunks stay remote."""
        config = source.config
        remote_base_id = getattr(config, "weknora_base_id", "")
        try:
            documents = await self._require_engine().list_documents(remote_base_id)
        except KnowledgeEngineError as error:
            completed_at = self._clock()
            failed = sync.model_copy(
                update={
                    "status": KnowledgeSyncStatus.FAILED,
                    "error_code": type(error).__name__,
                    "error_message": str(error)[:1_000],
                    "completed_at": completed_at,
                }
            )
            await self.repository.put_sync(failed)
            degraded = source.model_copy(
                update={
                    "revision": source.revision + 1,
                    "health": KnowledgeSourceHealth.DEGRADED,
                    "last_sync_id": sync.sync_id,
                    "last_sync_at": completed_at,
                    "last_error": str(error)[:1_000],
                    "updated_at": completed_at,
                }
            )
            await self.repository.compare_and_set_source(source.revision, degraded)
            return failed
        completed_at = self._clock()
        parse_completed = sum(1 for item in documents if item.parse_status == "completed")
        completed = sync.model_copy(
            update={
                "status": KnowledgeSyncStatus.SUCCEEDED,
                "checkpoint_after": {
                    "documents": len(documents),
                    "parse_completed": parse_completed,
                },
                "documents_seen": len(documents),
                "completed_at": completed_at,
            }
        )
        updated_source = source.model_copy(
            update={
                "revision": source.revision + 1,
                "health": KnowledgeSourceHealth.HEALTHY,
                "checkpoint": {
                    "documents": len(documents),
                    "parse_completed": parse_completed,
                },
                "last_sync_id": sync.sync_id,
                "last_sync_at": completed_at,
                "last_error": None,
                "updated_at": completed_at,
            }
        )
        if not await self.repository.compare_and_set_source(source.revision, updated_source):
            raise ConflictError("knowledge source changed while sync completed")
        await self.repository.put_sync(completed)
        return completed

    async def _accessible_weknora_source(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> KnowledgeSource:
        source = await self.repository.get_source(tenant_id, reference)
        if source.kind.value != "weknora":
            raise NotFoundError(f"knowledge source not found: {reference}")
        if source.created_by == actor_id:
            return source
        if source.acl.visibility is KnowledgeVisibility.TENANT or source.acl.allows(actor_id):
            return source
        # Engine bases keep a 1:1 link source named after the base, so a member
        # grant on the base also unlocks its documents, chunks and wiki.
        if await self._member_role(tenant_id, actor_id, reference) is not None:
            return source
        raise NotFoundError(f"knowledge source not found: {reference}")

    async def list_source_documents(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> list[KnowledgeDocumentStatus]:
        source = await self._accessible_weknora_source(tenant_id, actor_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        documents = await self._require_engine().list_documents(remote_id)
        return [
            KnowledgeDocumentStatus(
                tenantId=tenant_id,
                sourceReference=reference,
                documentId=item.document_id,
                title=item.title,
                parseStatus=item.parse_status,
                summaryStatus=item.summary_status,
                fileType=item.file_type,
                fileSize=item.file_size,
                enabled=item.enabled,
                createdAt=item.created_at,
                description=item.description,
            )
            for item in documents
        ]

    async def create_source_document(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        title: str,
        content: str,
    ) -> KnowledgeDocumentStatus:
        await self._require_editor(tenant_id, actor_id, reference)
        source = await self.repository.get_source(tenant_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        document_id = await self._require_engine().create_manual_document(
            remote_id,
            title=title,
            content=content,
        )
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.document.create",
            reference,
            {"document_id": document_id, "kind": "manual"},
        )
        await self._refresh_document_count(tenant_id, reference, source)
        return await self.get_source_document(tenant_id, actor_id, reference, document_id)

    async def upload_source_document(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        filename: str,
        content: bytes,
    ) -> KnowledgeDocumentStatus:
        await self._require_editor(tenant_id, actor_id, reference)
        source = await self.repository.get_source(tenant_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        document_id = await self._require_engine().upload_document(
            remote_id,
            filename=filename,
            content=content,
        )
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.document.create",
            reference,
            {"document_id": document_id, "kind": "file", "filename": filename[:200]},
        )
        await self._refresh_document_count(tenant_id, reference, source)
        return await self.get_source_document(tenant_id, actor_id, reference, document_id)

    async def get_source_document(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        document_id: str,
    ) -> KnowledgeDocumentStatus:
        await self._accessible_weknora_source(tenant_id, actor_id, reference)
        item = await self._require_engine().get_document(document_id)
        return KnowledgeDocumentStatus(
            tenantId=tenant_id,
            sourceReference=reference,
            documentId=item.document_id,
            title=item.title,
            parseStatus=item.parse_status,
            summaryStatus=item.summary_status,
            fileType=item.file_type,
            fileSize=item.file_size,
            enabled=item.enabled,
            createdAt=item.created_at,
            description=item.description,
        )

    async def delete_source_document(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        document_id: str,
    ) -> None:
        await self._require_editor(tenant_id, actor_id, reference)
        source = await self.repository.get_source(tenant_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        await self._require_engine().delete_document(remote_id, document_id)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.document.delete",
            reference,
            {"document_id": document_id},
        )
        await self._refresh_document_count(tenant_id, reference, source)

    async def reparse_source_document(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        document_id: str,
    ) -> None:
        await self._require_editor(tenant_id, actor_id, reference)
        source = await self.repository.get_source(tenant_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        await self._require_engine().reparse_document(remote_id, document_id)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.document.reparse",
            reference,
            {"document_id": document_id},
        )

    async def list_source_chunks(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        document_id: str,
    ) -> list[KnowledgeDocumentChunk]:
        await self._accessible_weknora_source(tenant_id, actor_id, reference)
        chunks = await self._require_engine().list_chunks(document_id)
        title = document_id
        if chunks:
            try:
                document = await self._require_engine().get_document(document_id)
                title = document.title
            except KnowledgeEngineError:
                title = document_id
        return [
            KnowledgeDocumentChunk(
                tenantId=tenant_id,
                sourceReference=reference,
                documentId=document_id,
                chunkId=item.chunk_id,
                title=title,
                content=item.content,
                seq=item.seq,
            )
            for item in chunks
        ]

    async def get_source_document_table(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        document_id: str,
    ) -> KnowledgeDocumentTable:
        """Render a spreadsheet document as a cell grid.

        WeKnora's chunk text flattens spreadsheets to ``A: value`` lines, so the
        table is rebuilt from the downloadable original file.
        """
        await self._accessible_weknora_source(tenant_id, actor_id, reference)
        document = await self._require_engine().get_document(document_id)
        filename = document.title or document.document_id
        if not is_spreadsheet(filename):
            raise ConflictError("document is not a spreadsheet")
        content = await self._require_engine().download_document(document_id)
        grid = parse_spreadsheet(filename, content)
        if grid is None:
            raise ConflictError("spreadsheet could not be parsed")
        return KnowledgeDocumentTable(
            tenantId=tenant_id,
            sourceReference=reference,
            documentId=document_id,
            title=document.title,
            sheet=grid.sheet,
            rows=grid.rows,
            truncated=grid.truncated,
            extraSheets=grid.extra_sheets,
        )

    async def get_source_chunk(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        chunk_id: str,
    ) -> KnowledgeDocumentChunk:
        await self._accessible_weknora_source(tenant_id, actor_id, reference)
        chunk = await self._require_engine().get_chunk(chunk_id)
        if chunk is None:
            raise NotFoundError("knowledge chunk not found")
        title = chunk.document_id
        try:
            document = await self._require_engine().get_document(chunk.document_id)
            title = document.title
        except KnowledgeEngineError:
            title = chunk.document_id
        return KnowledgeDocumentChunk(
            tenantId=tenant_id,
            sourceReference=reference,
            documentId=chunk.document_id,
            chunkId=chunk.chunk_id,
            title=title,
            content=chunk.content,
            seq=chunk.seq,
        )

    def _chunk_documents(
        self,
        *,
        tenant_id: str,
        source: KnowledgeSource,
        snapshot_id: str,
        documents: Sequence[object],
    ) -> tuple[KnowledgeChunk, ...]:
        from harness.knowledge.models import ConnectorDocument

        chunks: list[KnowledgeChunk] = []
        now = self._clock()
        for raw_document in documents:
            document = ConnectorDocument.model_validate(raw_document)
            content = "\n".join(
                line.rstrip() for line in document.content.replace("\r\n", "\n").splitlines()
            ).strip()
            if not content:
                continue
            start = 0
            ordinal = 0
            while start < len(content):
                stop = min(len(content), start + self._chunk_characters)
                if stop < len(content):
                    boundary = max(
                        content.rfind("\n", start, stop),
                        content.rfind("。", start, stop),
                        content.rfind(". ", start, stop),
                    )
                    if boundary > start + self._chunk_characters // 2:
                        stop = boundary + 1
                chunk_content = content[start:stop].strip()
                if chunk_content:
                    chunk_hash = hashlib.sha256(chunk_content.encode()).hexdigest()
                    chunk_id = hashlib.sha256(
                        (
                            f"{source.reference}\0{document.document_id}\0{ordinal}\0{chunk_hash}"
                        ).encode()
                    ).hexdigest()[:40]
                    chunks.append(
                        KnowledgeChunk(
                            tenantId=tenant_id,
                            snapshotId=snapshot_id,
                            sourceReference=source.reference,
                            chunkId=chunk_id,
                            documentId=document.document_id,
                            ordinal=ordinal,
                            title=document.title,
                            sourceUri=document.source_uri,
                            content=chunk_content,
                            contentHash=chunk_hash,
                            tokenTerms=tokenize(f"{document.title}\n{chunk_content}"),
                            createdAt=now,
                        )
                    )
                    ordinal += 1
                if stop >= len(content):
                    break
                start = max(start + 1, stop - self._chunk_overlap)
        return tuple(chunks)

    async def _require_sources(
        self,
        tenant_id: str,
        references: Sequence[str],
        *,
        owner_user_id: str | None = None,
    ) -> None:
        for reference in references:
            if owner_user_id is None:
                await self.repository.get_source(tenant_id, reference)
            else:
                await self._get_owned_source(tenant_id, owner_user_id, reference)

    @staticmethod
    def _personal_acl(actor_id: str, requested: KnowledgeAcl) -> KnowledgeAcl:
        explicitly_allowed = (
            requested.user_ids if requested.visibility is KnowledgeVisibility.RESTRICTED else ()
        )
        return KnowledgeAcl(
            visibility=KnowledgeVisibility.RESTRICTED,
            userIds=tuple(dict.fromkeys((actor_id, *explicitly_allowed))),
            workloadIds=requested.workload_ids,
        )

    async def _get_owned_base(
        self,
        tenant_id: str,
        owner_user_id: str,
        reference: str,
    ) -> KnowledgeBase:
        value = await self.repository.get_base(tenant_id, reference)
        if value.created_by != owner_user_id:
            raise NotFoundError(f"Knowledge Base not found: {reference}")
        return value

    async def _get_owned_source(
        self,
        tenant_id: str,
        owner_user_id: str,
        reference: str,
    ) -> KnowledgeSource:
        value = await self.repository.get_source(tenant_id, reference)
        if value.created_by != owner_user_id:
            raise NotFoundError(f"knowledge source not found: {reference}")
        return value

    async def list_wiki_pages(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> list[KnowledgeWikiPage]:
        source = await self._accessible_weknora_source(tenant_id, actor_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        pages = await self._require_engine().list_wiki_pages(remote_id)
        return [
            KnowledgeWikiPage(
                slug=item.slug,
                title=item.title,
                pageType=item.page_type,
                content=item.content,
                summary=item.summary,
                aliases=item.aliases,
                categoryPath=item.category_path,
                folderId=item.folder_id,
            )
            for item in pages
        ]

    async def get_wiki_page(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        slug: str,
    ) -> KnowledgeWikiPage:
        source = await self._accessible_weknora_source(tenant_id, actor_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        item = await self._require_engine().get_wiki_page(remote_id, slug)
        return KnowledgeWikiPage(
            slug=item.slug,
            title=item.title,
            pageType=item.page_type,
            content=item.content,
            summary=item.summary,
            aliases=item.aliases,
            categoryPath=item.category_path,
            folderId=item.folder_id,
        )

    async def search_wiki_pages(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        query: str,
        *,
        limit: int = 20,
    ) -> list[KnowledgeWikiPage]:
        source = await self._accessible_weknora_source(tenant_id, actor_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        pages = await self._require_engine().search_wiki_pages(
            remote_id,
            query,
            limit=max(1, min(limit, 50)),
        )
        return [
            KnowledgeWikiPage(
                slug=item.slug,
                title=item.title,
                pageType=item.page_type,
                content=item.content,
                summary=item.summary,
                aliases=item.aliases,
                categoryPath=item.category_path,
                folderId=item.folder_id,
            )
            for item in pages
        ]

    async def search_bound_wiki_pages(
        self,
        tenant_id: str,
        actor_id: str,
        bindings: Sequence[KnowledgeSnapshotBinding],
        query: str,
        *,
        limit: int = 8,
    ) -> list[KnowledgeWikiPage]:
        """Wiki-page search across the knowledge bases bound to a session.

        Wiki mode answers from curated pages (summary/entity/concept) instead of
        raw chunks, so the reply can cite and link wiki entities.
        """
        pages: list[KnowledgeWikiPage] = []
        seen: set[str] = set()
        for binding in bindings:
            source = await self.repository.get_source(tenant_id, binding.source_reference)
            if source.kind.value != "weknora":
                continue
            if not await self._allows_source(
                tenant_id,
                actor_id,
                (),
                binding.knowledge_base_reference,
                source,
            ):
                continue
            remote_id = getattr(source.config, "weknora_base_id", "")
            if not remote_id:
                continue
            try:
                found = await self._require_engine().search_wiki_pages(
                    remote_id,
                    query,
                    limit=max(1, min(limit, 25)),
                )
            except KnowledgeEngineError:
                continue
            for item in found:
                if item.slug in seen:
                    continue
                seen.add(item.slug)
                pages.append(
                    KnowledgeWikiPage(
                        slug=item.slug,
                        title=item.title,
                        pageType=item.page_type,
                        content=item.content,
                        summary=item.summary,
                        aliases=item.aliases,
                        categoryPath=item.category_path,
                        folderId=item.folder_id,
                    )
                )
        return pages

    async def wiki_graph(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> KnowledgeWikiGraph:
        source = await self._accessible_weknora_source(tenant_id, actor_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        graph = await self._require_engine().wiki_graph(remote_id)
        return KnowledgeWikiGraph(
            nodes=tuple(
                KnowledgeWikiGraphNode(
                    slug=node.slug,
                    title=node.title,
                    pageType=node.page_type,
                    linkCount=node.link_count,
                )
                for node in graph.nodes
            ),
            links=graph.links,
        )

    async def wiki_stats(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> KnowledgeWikiStats:
        source = await self._accessible_weknora_source(tenant_id, actor_id, reference)
        remote_id = getattr(source.config, "weknora_base_id", "")
        stats = await self._require_engine().wiki_stats(remote_id)
        return KnowledgeWikiStats(
            totalPages=stats.total_pages,
            pagesByType=stats.pages_by_type,
            totalLinks=stats.total_links,
        )

    # --- membership (phase 1: per-user viewer/editor) ---------------------

    async def list_members(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> list[KnowledgeBaseMember]:
        base = await self.repository.get_base(tenant_id, reference)
        if base.created_by != actor_id:
            # Members may see the roster; non-members must not enumerate it.
            role = await self._member_role(tenant_id, actor_id, reference)
            if role is None:
                raise NotFoundError(f"Knowledge Base not found: {reference}")
        return list(
            await self.repository.list_members(
                tenant_id,
                knowledge_base_reference=reference,
            )
        )

    async def add_members(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        request: AddKnowledgeMembersRequest,
    ) -> AddKnowledgeMembersResult:
        await self._get_owned_base(tenant_id, actor_id, reference)
        if self._directory is None:
            raise ConflictError("knowledge base member directory is not configured")
        resolved = await self._directory.resolve_users(
            user_ids=request.user_ids,
            emails=request.emails,
        )
        known_emails = {item.email.lower() for item in resolved}
        known_ids = {item.user_id for item in resolved}
        unresolved = tuple(
            item
            for item in (*request.user_ids, *request.emails)
            if item not in known_ids and item.lower() not in known_emails
        )
        existing = {
            item.subject_id
            for item in await self.repository.list_members(
                tenant_id,
                knowledge_base_reference=reference,
            )
            if item.subject_type is KnowledgeMemberSubject.USER
        }
        granted: list[KnowledgeBaseMember] = []
        now = self._clock()
        for user in resolved:
            if user.user_id in existing:
                continue
            member = KnowledgeBaseMember(
                tenantId=tenant_id,
                memberId=self._ids("knowledge_member"),
                knowledgeBaseReference=reference,
                subjectType=KnowledgeMemberSubject.USER,
                subjectId=user.user_id,
                role=request.role,
                displayName=user.display_name,
                email=user.email,
                grantedBy=actor_id,
                grantedAt=now,
            )
            await self.repository.add_member(member)
            granted.append(member)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.member.add",
            reference,
            {
                "granted": len(granted),
                "unresolved": len(unresolved),
                "role": request.role.value,
            },
        )
        return AddKnowledgeMembersResult(members=tuple(granted), unresolved=unresolved)

    async def update_member_role(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        member_id: str,
        role: KnowledgeMemberRole,
    ) -> KnowledgeBaseMember:
        await self._get_owned_base(tenant_id, actor_id, reference)
        member = await self.repository.get_member(tenant_id, member_id)
        if member.knowledge_base_reference != reference:
            raise NotFoundError(f"knowledge base member not found: {member_id}")
        updated = member.model_copy(update={"role": role})
        await self.repository.put_member(updated)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.member.update",
            reference,
            {"member_id": member_id, "role": role.value},
        )
        return updated

    async def remove_member(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
        member_id: str,
    ) -> None:
        await self._get_owned_base(tenant_id, actor_id, reference)
        member = await self.repository.get_member(tenant_id, member_id)
        if member.knowledge_base_reference != reference:
            raise NotFoundError(f"knowledge base member not found: {member_id}")
        await self.repository.delete_member(tenant_id, member_id)
        await self._record(
            tenant_id,
            actor_id,
            "knowledge.member.remove",
            reference,
            {"member_id": member_id},
        )

    async def search_directory_users(
        self,
        tenant_id: str,
        actor_id: str,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        if self._directory is None:
            return []
        users = await self._directory.search_users(query, limit=limit)
        return [
            {"userId": item.user_id, "email": item.email, "displayName": item.display_name}
            for item in users
        ]

    async def _member_role(
        self,
        tenant_id: str,
        actor_id: str,
        reference: str,
    ) -> KnowledgeMemberRole | None:
        members = await self.repository.list_members(
            tenant_id,
            knowledge_base_reference=reference,
        )
        for member in members:
            if member.subject_type is KnowledgeMemberSubject.USER and member.subject_id == actor_id:
                return member.role
        return None

    async def _record(
        self,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_id: str,
        details: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            tenant_id=tenant_id,
            user_id=actor_id,
            action=action,
            resource_type="knowledge",
            resource_id=resource_id,
            outcome=str(details.get("outcome", "success")),
            details=details,
        )
