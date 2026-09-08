from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.knowledge.models import (
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeChunk,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeSyncRun,
)
from harness.storage.database import SessionFactory
from harness.storage.models import (
    KnowledgeBaseMemberRow,
    KnowledgeBaseRow,
    KnowledgeChunkRow,
    KnowledgeSnapshotRow,
    KnowledgeSourceRow,
    KnowledgeSyncRunRow,
)


class PostgresKnowledgeRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add_base(self, value: KnowledgeBase) -> None:
        async with self._sessions() as db:
            db.add(
                KnowledgeBaseRow(
                    tenant_id=value.tenant_id,
                    reference=value.reference,
                    revision=value.revision,
                    updated_at=value.updated_at,
                    payload=value.model_dump(mode="json", by_alias=True),
                )
            )
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(f"Knowledge Base already exists: {value.reference}") from error

    async def get_base(self, tenant_id: str, reference: str) -> KnowledgeBase:
        async with self._sessions() as db:
            row = await db.get(KnowledgeBaseRow, (tenant_id, reference))
            if row is None:
                raise NotFoundError(f"Knowledge Base not found: {reference}")
            return KnowledgeBase.model_validate(row.payload)

    async def list_bases(self, tenant_id: str) -> Sequence[KnowledgeBase]:
        statement = (
            select(KnowledgeBaseRow)
            .where(KnowledgeBaseRow.tenant_id == tenant_id)
            .order_by(KnowledgeBaseRow.reference)
        )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(KnowledgeBase.model_validate(row.payload) for row in rows)

    async def compare_and_set_base(self, expected_revision: int, value: KnowledgeBase) -> bool:
        if value.revision != expected_revision + 1:
            raise ConflictError("Knowledge Base revision must increment by one")
        statement = (
            update(KnowledgeBaseRow)
            .where(
                KnowledgeBaseRow.tenant_id == value.tenant_id,
                KnowledgeBaseRow.reference == value.reference,
                KnowledgeBaseRow.revision == expected_revision,
            )
            .values(
                revision=value.revision,
                updated_at=value.updated_at,
                payload=value.model_dump(mode="json", by_alias=True),
            )
        )
        async with self._sessions() as db:
            result = await db.execute(statement)
            await db.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def add_source(self, value: KnowledgeSource) -> None:
        async with self._sessions() as db:
            db.add(self._source_row(value))
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(
                    f"knowledge source already exists: {value.reference}"
                ) from error

    async def get_source(self, tenant_id: str, reference: str) -> KnowledgeSource:
        async with self._sessions() as db:
            row = await db.get(KnowledgeSourceRow, (tenant_id, reference))
            if row is None:
                raise NotFoundError(f"knowledge source not found: {reference}")
            return KnowledgeSource.model_validate(row.payload)

    async def list_sources(self, tenant_id: str) -> Sequence[KnowledgeSource]:
        statement = (
            select(KnowledgeSourceRow)
            .where(KnowledgeSourceRow.tenant_id == tenant_id)
            .order_by(KnowledgeSourceRow.reference)
        )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(KnowledgeSource.model_validate(row.payload) for row in rows)

    async def compare_and_set_source(self, expected_revision: int, value: KnowledgeSource) -> bool:
        if value.revision != expected_revision + 1:
            raise ConflictError("knowledge source revision must increment by one")
        statement = (
            update(KnowledgeSourceRow)
            .where(
                KnowledgeSourceRow.tenant_id == value.tenant_id,
                KnowledgeSourceRow.reference == value.reference,
                KnowledgeSourceRow.revision == expected_revision,
            )
            .values(
                kind=value.kind.value,
                health=value.health.value,
                revision=value.revision,
                active_snapshot_id=value.active_snapshot_id,
                updated_at=value.updated_at,
                payload=value.model_dump(mode="json", by_alias=True),
            )
        )
        async with self._sessions() as db:
            result = await db.execute(statement)
            await db.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def add_sync(self, value: KnowledgeSyncRun) -> None:
        async with self._sessions() as db:
            db.add(self._sync_row(value))
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(f"knowledge sync already exists: {value.sync_id}") from error

    async def put_sync(self, value: KnowledgeSyncRun) -> None:
        statement = (
            update(KnowledgeSyncRunRow)
            .where(
                KnowledgeSyncRunRow.tenant_id == value.tenant_id,
                KnowledgeSyncRunRow.sync_id == value.sync_id,
            )
            .values(
                status=value.status.value,
                payload=value.model_dump(mode="json", by_alias=True),
            )
        )
        async with self._sessions() as db:
            result = await db.execute(statement)
            if not cast(CursorResult[Any], result).rowcount:
                await db.rollback()
                raise NotFoundError(f"knowledge sync not found: {value.sync_id}")
            await db.commit()

    async def get_sync(self, tenant_id: str, sync_id: str) -> KnowledgeSyncRun:
        async with self._sessions() as db:
            row = await db.get(KnowledgeSyncRunRow, (tenant_id, sync_id))
            if row is None:
                raise NotFoundError(f"knowledge sync not found: {sync_id}")
            return KnowledgeSyncRun.model_validate(row.payload)

    async def list_syncs(
        self,
        tenant_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSyncRun]:
        statement = select(KnowledgeSyncRunRow).where(KnowledgeSyncRunRow.tenant_id == tenant_id)
        if source_reference is not None:
            statement = statement.where(KnowledgeSyncRunRow.source_reference == source_reference)
        statement = statement.order_by(
            KnowledgeSyncRunRow.created_at.desc(),
            KnowledgeSyncRunRow.sync_id.desc(),
        ).limit(limit)
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(KnowledgeSyncRun.model_validate(row.payload) for row in rows)

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
        statement = (
            update(KnowledgeSourceRow)
            .where(
                KnowledgeSourceRow.tenant_id == source.tenant_id,
                KnowledgeSourceRow.reference == source.reference,
                KnowledgeSourceRow.revision == expected_source_revision,
            )
            .values(
                health=source.health.value,
                revision=source.revision,
                active_snapshot_id=source.active_snapshot_id,
                updated_at=source.updated_at,
                payload=source.model_dump(mode="json", by_alias=True),
            )
        )
        async with self._sessions() as db:
            result = await db.execute(statement)
            if not cast(CursorResult[Any], result).rowcount:
                await db.rollback()
                return False
            db.add(
                KnowledgeSnapshotRow(
                    tenant_id=snapshot.tenant_id,
                    snapshot_id=snapshot.snapshot_id,
                    source_reference=snapshot.source_reference,
                    content_hash=snapshot.content_hash,
                    created_at=snapshot.created_at,
                    payload=snapshot.model_dump(mode="json", by_alias=True),
                )
            )
            db.add_all(
                KnowledgeChunkRow(
                    tenant_id=item.tenant_id,
                    snapshot_id=item.snapshot_id,
                    chunk_id=item.chunk_id,
                    source_reference=item.source_reference,
                    document_id=item.document_id,
                    ordinal=item.ordinal,
                    content=item.content,
                    payload=item.model_dump(mode="json", by_alias=True),
                )
                for item in chunks
            )
            sync_result = await db.execute(
                update(KnowledgeSyncRunRow)
                .where(
                    KnowledgeSyncRunRow.tenant_id == sync.tenant_id,
                    KnowledgeSyncRunRow.sync_id == sync.sync_id,
                )
                .values(
                    status=sync.status.value,
                    payload=sync.model_dump(mode="json", by_alias=True),
                )
            )
            if not cast(CursorResult[Any], sync_result).rowcount:
                await db.rollback()
                raise NotFoundError(f"knowledge sync not found: {sync.sync_id}")
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(
                    f"knowledge snapshot already exists: {snapshot.snapshot_id}"
                ) from error
            return True

    async def get_snapshot(self, tenant_id: str, snapshot_id: str) -> KnowledgeSnapshot:
        async with self._sessions() as db:
            row = await db.get(KnowledgeSnapshotRow, (tenant_id, snapshot_id))
            if row is None:
                raise NotFoundError(f"knowledge snapshot not found: {snapshot_id}")
            return KnowledgeSnapshot.model_validate(row.payload)

    async def list_snapshots(
        self,
        tenant_id: str,
        *,
        source_reference: str | None = None,
        limit: int = 100,
    ) -> Sequence[KnowledgeSnapshot]:
        statement = select(KnowledgeSnapshotRow).where(KnowledgeSnapshotRow.tenant_id == tenant_id)
        if source_reference is not None:
            statement = statement.where(KnowledgeSnapshotRow.source_reference == source_reference)
        statement = statement.order_by(
            KnowledgeSnapshotRow.created_at.desc(),
            KnowledgeSnapshotRow.snapshot_id.desc(),
        ).limit(limit)
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(KnowledgeSnapshot.model_validate(row.payload) for row in rows)

    async def list_chunks(
        self,
        tenant_id: str,
        snapshot_ids: frozenset[str],
    ) -> Sequence[KnowledgeChunk]:
        if not snapshot_ids:
            return ()
        statement = (
            select(KnowledgeChunkRow)
            .where(
                KnowledgeChunkRow.tenant_id == tenant_id,
                KnowledgeChunkRow.snapshot_id.in_(tuple(snapshot_ids)),
            )
            .order_by(
                KnowledgeChunkRow.snapshot_id,
                KnowledgeChunkRow.document_id,
                KnowledgeChunkRow.ordinal,
            )
        )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(KnowledgeChunk.model_validate(row.payload) for row in rows)

    async def add_member(self, value: KnowledgeBaseMember) -> None:
        async with self._sessions() as db:
            db.add(
                KnowledgeBaseMemberRow(
                    tenant_id=value.tenant_id,
                    member_id=value.member_id,
                    knowledge_base_reference=value.knowledge_base_reference,
                    subject_type=value.subject_type.value,
                    subject_id=value.subject_id,
                    role=value.role.value,
                    created_at=value.granted_at,
                    payload=value.model_dump(mode="json", by_alias=True),
                )
            )
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(
                    f"knowledge base member already granted: {value.subject_id}"
                ) from error

    async def get_member(self, tenant_id: str, member_id: str) -> KnowledgeBaseMember:
        async with self._sessions() as db:
            row = await db.get(KnowledgeBaseMemberRow, (tenant_id, member_id))
            if row is None:
                raise NotFoundError(f"knowledge base member not found: {member_id}")
            return KnowledgeBaseMember.model_validate(row.payload)

    async def list_members(
        self,
        tenant_id: str,
        *,
        knowledge_base_reference: str | None = None,
    ) -> Sequence[KnowledgeBaseMember]:
        statement = select(KnowledgeBaseMemberRow).where(
            KnowledgeBaseMemberRow.tenant_id == tenant_id
        )
        if knowledge_base_reference is not None:
            statement = statement.where(
                KnowledgeBaseMemberRow.knowledge_base_reference == knowledge_base_reference
            )
        statement = statement.order_by(
            KnowledgeBaseMemberRow.knowledge_base_reference,
            KnowledgeBaseMemberRow.subject_id,
        )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(KnowledgeBaseMember.model_validate(row.payload) for row in rows)

    async def put_member(self, value: KnowledgeBaseMember) -> None:
        statement = (
            update(KnowledgeBaseMemberRow)
            .where(
                KnowledgeBaseMemberRow.tenant_id == value.tenant_id,
                KnowledgeBaseMemberRow.member_id == value.member_id,
            )
            .values(
                role=value.role.value,
                payload=value.model_dump(mode="json", by_alias=True),
            )
        )
        async with self._sessions() as db:
            result = await db.execute(statement)
            if not cast(CursorResult[Any], result).rowcount:
                await db.rollback()
                raise NotFoundError(f"knowledge base member not found: {value.member_id}")
            await db.commit()

    async def delete_member(self, tenant_id: str, member_id: str) -> bool:
        statement = delete(KnowledgeBaseMemberRow).where(
            KnowledgeBaseMemberRow.tenant_id == tenant_id,
            KnowledgeBaseMemberRow.member_id == member_id,
        )
        async with self._sessions() as db:
            result = await db.execute(statement)
            await db.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    @staticmethod
    def _source_row(value: KnowledgeSource) -> KnowledgeSourceRow:
        return KnowledgeSourceRow(
            tenant_id=value.tenant_id,
            reference=value.reference,
            kind=value.kind.value,
            health=value.health.value,
            revision=value.revision,
            active_snapshot_id=value.active_snapshot_id,
            updated_at=value.updated_at,
            payload=value.model_dump(mode="json", by_alias=True),
        )

    @staticmethod
    def _sync_row(value: KnowledgeSyncRun) -> KnowledgeSyncRunRow:
        return KnowledgeSyncRunRow(
            tenant_id=value.tenant_id,
            sync_id=value.sync_id,
            source_reference=value.source_reference,
            status=value.status.value,
            created_at=value.created_at,
            payload=value.model_dump(mode="json", by_alias=True),
        )
