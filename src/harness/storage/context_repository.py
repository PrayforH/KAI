"""PostgreSQL Session context state and immutable Digest persistence."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, desc, select, update
from sqlalchemy.exc import IntegrityError

from harness.context.models import SessionContextDigest, SessionContextState
from harness.context.repositories import (
    validate_digest_publication,
    validate_state_update,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from harness.storage.models import SessionContextDigestRow, SessionContextStateRow


class PostgresContextRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add_state(self, state: SessionContextState) -> None:
        async with self._sessions() as db:
            db.add(self._state_row(state))
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(
                    f"context state already exists: {state.session_id}"
                ) from error

    async def get_state(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextState:
        async with self._sessions() as db:
            row = await db.get(SessionContextStateRow, (tenant_id, session_id))
            if row is None or row.owner_user_id != owner_user_id:
                raise NotFoundError(f"context state not found: {session_id}")
            return SessionContextState.model_validate(row.payload)

    async def compare_and_set_state(
        self,
        expected_revision: int,
        state: SessionContextState,
    ) -> bool:
        if state.revision != expected_revision + 1:
            raise ConflictError("context state revision must increment by one")
        statement = (
            update(SessionContextStateRow)
            .where(
                SessionContextStateRow.tenant_id == state.tenant_id,
                SessionContextStateRow.session_id == state.session_id,
                SessionContextStateRow.owner_user_id == state.owner_user_id,
                SessionContextStateRow.revision == expected_revision,
            )
            .values(**self._state_values(state))
        )
        async with self._sessions() as db:
            current_row = await db.get(
                SessionContextStateRow,
                (state.tenant_id, state.session_id),
            )
            if current_row is None or current_row.owner_user_id != state.owner_user_id:
                raise NotFoundError(f"context state not found: {state.session_id}")
            current = SessionContextState.model_validate(current_row.payload)
            if current.revision != expected_revision:
                return False
            validate_state_update(expected_revision, current, state)
            result = await db.execute(statement)
            await db.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def publish_digest(
        self,
        expected_state_revision: int,
        state: SessionContextState,
        digest: SessionContextDigest,
    ) -> bool:
        if state.revision != expected_state_revision + 1:
            raise ConflictError("context state revision must increment by one")
        if (state.tenant_id, state.owner_user_id, state.session_id) != (
            digest.tenant_id,
            digest.owner_user_id,
            digest.session_id,
        ):
            raise ConflictError("context digest scope does not match state")
        async with self._sessions() as db:
            row = await db.get(
                SessionContextStateRow,
                (state.tenant_id, state.session_id),
                with_for_update=True,
            )
            if row is None or row.owner_user_id != state.owner_user_id:
                raise NotFoundError(f"context state not found: {state.session_id}")
            current = SessionContextState.model_validate(row.payload)
            if current.revision != expected_state_revision:
                await db.rollback()
                return False
            validate_digest_publication(
                expected_state_revision,
                current,
                state,
                digest,
            )
            db.add(
                SessionContextDigestRow(
                    tenant_id=digest.tenant_id,
                    session_id=digest.session_id,
                    version=digest.version,
                    digest_id=digest.digest_id,
                    owner_user_id=digest.owner_user_id,
                    content_hash=digest.content_hash,
                    transcript_checkpoint_hash=(
                        digest.source.transcript_checkpoint_hash
                    ),
                    created_at=digest.created_at,
                    payload=digest.model_dump(mode="json"),
                )
            )
            for key, value in self._state_values(state).items():
                setattr(row, key, value)
            try:
                await db.commit()
            except IntegrityError as error:
                await db.rollback()
                raise ConflictError(
                    f"context digest already exists: {digest.digest_id}"
                ) from error
            return True

    async def get_digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        digest_id: str,
    ) -> SessionContextDigest:
        statement = select(SessionContextDigestRow).where(
            SessionContextDigestRow.tenant_id == tenant_id,
            SessionContextDigestRow.owner_user_id == owner_user_id,
            SessionContextDigestRow.session_id == session_id,
            SessionContextDigestRow.digest_id == digest_id,
        )
        async with self._sessions() as db:
            row = await db.scalar(statement)
            if row is None:
                raise NotFoundError(f"context digest not found: {digest_id}")
            return SessionContextDigest.model_validate(row.payload)

    async def latest_digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextDigest | None:
        state = await self.get_state(tenant_id, owner_user_id, session_id)
        if state.latest_digest_id is None:
            return None
        return await self.get_digest(
            tenant_id,
            owner_user_id,
            session_id,
            state.latest_digest_id,
        )

    async def list_digests(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        *,
        before_version: int | None = None,
        limit: int = 20,
    ) -> list[SessionContextDigest]:
        await self.get_state(tenant_id, owner_user_id, session_id)
        statement = (
            select(SessionContextDigestRow)
            .where(
                SessionContextDigestRow.tenant_id == tenant_id,
                SessionContextDigestRow.owner_user_id == owner_user_id,
                SessionContextDigestRow.session_id == session_id,
            )
            .order_by(desc(SessionContextDigestRow.version))
            .limit(limit)
        )
        if before_version is not None:
            statement = statement.where(
                SessionContextDigestRow.version < before_version
            )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return [SessionContextDigest.model_validate(row.payload) for row in rows]

    @staticmethod
    def _state_values(state: SessionContextState) -> dict[str, object]:
        return {
            "owner_user_id": state.owner_user_id,
            "revision": state.revision,
            "trust_high_watermark": state.trust_high_watermark.value,
            "latest_digest_id": state.latest_digest_id,
            "latest_digest_version": state.latest_digest_version,
            "transcript_checkpoint_hash": state.transcript_checkpoint_hash,
            "updated_at": state.updated_at,
            "payload": state.model_dump(mode="json"),
        }

    @classmethod
    def _state_row(cls, state: SessionContextState) -> SessionContextStateRow:
        return SessionContextStateRow(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            **cls._state_values(state),
        )
