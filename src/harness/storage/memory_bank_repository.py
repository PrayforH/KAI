from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.core.errors import ConflictError, NotFoundError
from harness.memory_bank.models import (
    MemoryConsent,
    MemoryEntry,
    MemoryRetention,
    MemorySearchHit,
    MemoryStatus,
)
from harness.memory_bank.repositories import dedup_key
from harness.storage.database import SessionFactory
from harness.storage.models import (
    MemoryConsentRow,
    MemoryEmbeddingRow,
    MemoryEntryRow,
    MemoryExtractionJobRow,
    MemoryRetentionRow,
)


class PostgresMemoryBankRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add_entry(self, entry: MemoryEntry) -> None:
        async with self._sessions() as db:
            await self._lock_scope(db, entry)
            await self._validate_extraction(db, entry)
            db.add(
                MemoryEntryRow(
                    tenant_id=entry.tenant_id,
                    user_id=entry.user_id,
                    entry_id=entry.entry_id,
                    agent_name=entry.agent_name,
                    agent_owner_user_id=entry.owner_id,
                    dedup_key=dedup_key(entry),
                    status=entry.status.value,
                    version=entry.version,
                    updated_at=entry.updated_at,
                    expires_at=entry.expires_at,
                    payload=entry.model_dump(mode="json", by_alias=True),
                )
            )
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(f"memory entry already exists: {entry.entry_id}") from error

    async def _validate_extraction(self, db: AsyncSession, entry: MemoryEntry) -> None:
        if entry.source.extraction_job_id:
            removed_after_source = await db.scalar(
                select(MemoryEntryRow.entry_id)
                .where(
                    MemoryEntryRow.tenant_id == entry.tenant_id,
                    MemoryEntryRow.user_id == entry.user_id,
                    MemoryEntryRow.agent_name == entry.agent_name,
                    func.coalesce(MemoryEntryRow.agent_owner_user_id, MemoryEntryRow.user_id)
                    == entry.owner_id,
                    MemoryEntryRow.status.in_(["deleted", "rejected"]),
                    MemoryEntryRow.updated_at >= entry.source.captured_at,
                )
                .limit(1)
            )
            if removed_after_source is not None:
                raise ConflictError("source predates the memory deletion boundary")
            job = await db.scalar(
                select(MemoryExtractionJobRow)
                .where(
                    MemoryExtractionJobRow.tenant_id == entry.tenant_id,
                    MemoryExtractionJobRow.run_id == entry.source.extraction_job_id,
                )
                .with_for_update()
            )
            if (
                job is None
                or job.status != "processing"
                or job.attempts != entry.source.extraction_version
            ):
                raise ConflictError("memory extraction job is no longer current")

    async def get_entry(self, tenant_id: str, user_id: str, entry_id: str) -> MemoryEntry:
        async with self._sessions() as db:
            row = await db.get(MemoryEntryRow, (tenant_id, user_id, entry_id))
            if row is None:
                raise NotFoundError(f"memory entry not found: {entry_id}")
            return MemoryEntry.model_validate(row.payload)

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
        statement = select(MemoryEntryRow).where(
            MemoryEntryRow.tenant_id == tenant_id,
            MemoryEntryRow.user_id == user_id,
        )
        if agent_name is not None:
            statement = statement.where(MemoryEntryRow.agent_name == agent_name)
        if statuses is not None:
            statement = statement.where(
                MemoryEntryRow.status.in_(tuple(status.value for status in statuses))
            )
        if owner_id is not None:
            statement = statement.where(
                func.coalesce(MemoryEntryRow.agent_owner_user_id, MemoryEntryRow.user_id)
                == owner_id
            )
        if active_at is not None:
            statement = statement.where(
                or_(MemoryEntryRow.expires_at.is_(None), MemoryEntryRow.expires_at > active_at)
            )
        statement = statement.order_by(
            MemoryEntryRow.updated_at.desc(), MemoryEntryRow.entry_id.desc()
        ).limit(limit)
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(MemoryEntry.model_validate(row.payload) for row in rows)

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
        statement = (
            update(MemoryEntryRow)
            .where(
                MemoryEntryRow.tenant_id == updated.tenant_id,
                MemoryEntryRow.user_id == updated.user_id,
                MemoryEntryRow.entry_id == updated.entry_id,
                MemoryEntryRow.version == expected_version,
            )
            .values(
                status=updated.status.value,
                dedup_key=dedup_key(updated),
                agent_owner_user_id=updated.owner_id,
                version=updated.version,
                updated_at=updated.updated_at,
                expires_at=updated.expires_at,
                payload=updated.model_dump(mode="json", by_alias=True),
            )
        )
        async with self._sessions() as db:
            await self._lock_scope(db, updated)
            if extraction_update:
                await self._validate_extraction(db, updated)
            # Lock jobs before entries, matching the extraction insert order.
            if updated.status in {MemoryStatus.DELETED, MemoryStatus.REJECTED}:
                await db.execute(
                    update(MemoryExtractionJobRow)
                    .where(
                        MemoryExtractionJobRow.tenant_id == updated.tenant_id,
                        MemoryExtractionJobRow.user_id == updated.user_id,
                        MemoryExtractionJobRow.agent_name == updated.agent_name,
                        MemoryExtractionJobRow.agent_owner_user_id == updated.owner_id,
                        MemoryExtractionJobRow.created_at <= updated.updated_at,
                        MemoryExtractionJobRow.status.in_(["pending", "processing", "retrying"]),
                    )
                    .values(status="cancelled", error_code="memory_removed")
                )
            if superseded is not None:
                previous = await db.execute(
                    update(MemoryEntryRow)
                    .where(
                        MemoryEntryRow.tenant_id == superseded.tenant_id,
                        MemoryEntryRow.user_id == superseded.user_id,
                        MemoryEntryRow.entry_id == superseded.entry_id,
                        MemoryEntryRow.version == superseded.version - 1,
                        MemoryEntryRow.status == "active",
                    )
                    .values(
                        status=superseded.status.value,
                        version=superseded.version,
                        updated_at=superseded.updated_at,
                        dedup_key=None,
                        payload=superseded.model_dump(mode="json", by_alias=True),
                    )
                )
                if not cast(CursorResult[Any], previous).rowcount:
                    await db.rollback()
                    return False
            try:
                result = await db.execute(statement)
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError("a matching live memory already exists") from error
            if not cast(CursorResult[Any], result).rowcount:
                await db.rollback()
                return False
            ids = [updated.entry_id] + ([superseded.entry_id] if superseded else [])
            await db.execute(
                delete(MemoryEmbeddingRow).where(
                    MemoryEmbeddingRow.tenant_id == updated.tenant_id,
                    MemoryEmbeddingRow.user_id == updated.user_id,
                    MemoryEmbeddingRow.entry_id.in_(ids),
                )
            )
            await db.commit()
            return True

    async def list_expired(self, now: datetime, *, limit: int) -> Sequence[MemoryEntry]:
        statement = (
            select(MemoryEntryRow)
            .where(
                MemoryEntryRow.status == MemoryStatus.ACTIVE.value,
                MemoryEntryRow.expires_at.is_not(None),
                MemoryEntryRow.expires_at <= now,
            )
            .order_by(MemoryEntryRow.expires_at)
            .limit(limit)
        )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(MemoryEntry.model_validate(row.payload) for row in rows)

    async def get_consent(
        self, tenant_id: str, user_id: str, agent_name: str
    ) -> MemoryConsent | None:
        async with self._sessions() as db:
            row = await db.get(MemoryConsentRow, (tenant_id, user_id, agent_name))
            return None if row is None else MemoryConsent.model_validate(row.payload)

    async def put_consent(self, expected_version: int, consent: MemoryConsent) -> bool:
        return await self._put_policy(MemoryConsentRow, expected_version, consent.version, consent)

    async def get_retention(
        self, tenant_id: str, user_id: str, agent_name: str
    ) -> MemoryRetention | None:
        async with self._sessions() as db:
            row = await db.get(MemoryRetentionRow, (tenant_id, user_id, agent_name))
            return None if row is None else MemoryRetention.model_validate(row.payload)

    async def put_retention(self, expected_version: int, retention: MemoryRetention) -> bool:
        return await self._put_policy(
            MemoryRetentionRow, expected_version, retention.version, retention
        )

    async def _put_policy(
        self,
        row_type: type[MemoryConsentRow] | type[MemoryRetentionRow],
        expected_version: int,
        new_version: int,
        value: MemoryConsent | MemoryRetention,
    ) -> bool:
        if new_version != expected_version + 1:
            return False
        key = (value.tenant_id, value.user_id, value.agent_name)
        async with self._sessions() as db:
            if expected_version == 0:
                db.add(
                    row_type(
                        tenant_id=value.tenant_id,
                        user_id=value.user_id,
                        agent_name=value.agent_name,
                        version=new_version,
                        payload=value.model_dump(mode="json", by_alias=True),
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    return False
                return True
            statement = (
                update(row_type)
                .where(
                    row_type.tenant_id == key[0],
                    row_type.user_id == key[1],
                    row_type.agent_name == key[2],
                    row_type.version == expected_version,
                )
                .values(
                    version=new_version,
                    payload=value.model_dump(mode="json", by_alias=True),
                )
            )
            result = await db.execute(statement)
            await db.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def put_embedding(self, entry: MemoryEntry, model: str, vector: Sequence[float]) -> bool:
        async with self._sessions() as db:
            current = await db.scalar(
                select(MemoryEntryRow)
                .where(
                    MemoryEntryRow.tenant_id == entry.tenant_id,
                    MemoryEntryRow.user_id == entry.user_id,
                    MemoryEntryRow.entry_id == entry.entry_id,
                )
                .with_for_update()
            )
            if current is None or current.version != entry.version or current.status != "active":
                return False
            statement = insert(MemoryEmbeddingRow).values(
                tenant_id=entry.tenant_id,
                user_id=entry.user_id,
                entry_id=entry.entry_id,
                entry_version=entry.version,
                model=model,
                embedding=list(vector),
            )
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=["tenant_id", "user_id", "entry_id"],
                    set_={
                        "entry_version": entry.version,
                        "model": model,
                        "embedding": list(vector),
                    },
                )
            )
            await db.commit()
            return True

    @staticmethod
    def _embedding_join():
        return and_(
            MemoryEntryRow.tenant_id == MemoryEmbeddingRow.tenant_id,
            MemoryEntryRow.user_id == MemoryEmbeddingRow.user_id,
            MemoryEntryRow.entry_id == MemoryEmbeddingRow.entry_id,
        )

    async def unindexed(
        self, model: str, now: datetime, *, limit: int = 32
    ) -> Sequence[MemoryEntry]:
        statement = (
            select(MemoryEntryRow)
            .outerjoin(MemoryEmbeddingRow, self._embedding_join())
            .where(
                MemoryEntryRow.status == "active",
                or_(MemoryEntryRow.expires_at.is_(None), MemoryEntryRow.expires_at > now),
                or_(
                    MemoryEmbeddingRow.entry_id.is_(None),
                    MemoryEmbeddingRow.entry_version != MemoryEntryRow.version,
                    MemoryEmbeddingRow.model != model,
                ),
            )
            .order_by(MemoryEntryRow.updated_at)
            .limit(limit)
        )
        async with self._sessions() as db:
            return tuple(
                MemoryEntry.model_validate(r.payload) for r in (await db.scalars(statement)).all()
            )

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
        # Exact pgvector cosine ranking inside the authorized SQL scope. Avoid ANN
        # post-filter underfill; HNSW is optional after tenant-scale benchmarking.
        distance = MemoryEmbeddingRow.embedding.cosine_distance(list(vector))
        statement = (
            select(MemoryEntryRow, distance.label("distance"))
            .join(MemoryEmbeddingRow, self._embedding_join())
            .where(
                MemoryEntryRow.tenant_id == tenant_id,
                MemoryEntryRow.user_id == user_id,
                MemoryEntryRow.agent_name == agent_name,
                func.coalesce(MemoryEntryRow.agent_owner_user_id, MemoryEntryRow.user_id)
                == owner_id,
                MemoryEntryRow.status == "active",
                or_(MemoryEntryRow.expires_at.is_(None), MemoryEntryRow.expires_at > now),
                MemoryEmbeddingRow.model == model,
                MemoryEmbeddingRow.entry_version == MemoryEntryRow.version,
            )
            .order_by(distance, MemoryEntryRow.entry_id)
            .limit(limit)
        )
        async with self._sessions() as db:
            rows = (await db.execute(statement)).all()
            return tuple(
                MemorySearchHit(
                    entry=MemoryEntry.model_validate(row.payload),
                    score=max(0.0, min(1.0, 1.0 - float(d))),
                )
                for row, d in rows
            )

    @staticmethod
    async def _lock_scope(db: AsyncSession, entry: MemoryEntry) -> None:
        digest = hashlib.sha256(
            f"{entry.tenant_id}\0{entry.user_id}\0{entry.owner_id}\0{entry.agent_name}".encode()
        ).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
