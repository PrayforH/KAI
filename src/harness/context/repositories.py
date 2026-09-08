"""Persistence contracts and deterministic in-memory context storage."""

from __future__ import annotations

import asyncio
from typing import Protocol

from harness.context.models import SessionContextDigest, SessionContextState
from harness.core.errors import ConflictError, NotFoundError
from harness.policy.models import ContextTrust

_TRUST_PRECEDENCE = {
    ContextTrust.SAFE: 0,
    ContextTrust.SENSITIVE: 1,
    ContextTrust.UNTRUSTED: 2,
}


def validate_state_update(
    expected_revision: int,
    current: SessionContextState,
    state: SessionContextState,
) -> None:
    if state.revision != expected_revision + 1:
        raise ConflictError("context state revision must increment by one")
    if (state.tenant_id, state.owner_user_id, state.session_id) != (
        current.tenant_id,
        current.owner_user_id,
        current.session_id,
    ):
        raise ConflictError("context state scope cannot change")
    if (
        _TRUST_PRECEDENCE[state.trust_high_watermark]
        < _TRUST_PRECEDENCE[current.trust_high_watermark]
    ):
        raise ConflictError("context trust high-water mark cannot decrease")


def validate_digest_publication(
    expected_revision: int,
    current: SessionContextState,
    state: SessionContextState,
    digest: SessionContextDigest,
) -> None:
    validate_state_update(expected_revision, current, state)
    if (state.tenant_id, state.owner_user_id, state.session_id) != (
        digest.tenant_id,
        digest.owner_user_id,
        digest.session_id,
    ):
        raise ConflictError("context digest scope does not match state")
    if digest.version != current.latest_digest_version + 1:
        raise ConflictError("context digest version must increment by one")
    if (
        state.latest_digest_id != digest.digest_id
        or state.latest_digest_version != digest.version
        or state.transcript_checkpoint_hash != digest.source.transcript_checkpoint_hash
        or state.trust_high_watermark != digest.trust_high_watermark
    ):
        raise ConflictError("context state does not point to published digest")


class ContextRepository(Protocol):
    async def add_state(self, state: SessionContextState) -> None: ...

    async def get_state(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextState: ...

    async def compare_and_set_state(
        self,
        expected_revision: int,
        state: SessionContextState,
    ) -> bool: ...

    async def publish_digest(
        self,
        expected_state_revision: int,
        state: SessionContextState,
        digest: SessionContextDigest,
    ) -> bool: ...

    async def get_digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        digest_id: str,
    ) -> SessionContextDigest: ...

    async def latest_digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextDigest | None: ...

    async def list_digests(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        *,
        before_version: int | None = None,
        limit: int = 20,
    ) -> list[SessionContextDigest]: ...


class InMemoryContextRepository:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], SessionContextState] = {}
        self._digests: dict[tuple[str, str, int], SessionContextDigest] = {}
        self._lock = asyncio.Lock()

    async def add_state(self, state: SessionContextState) -> None:
        key = (state.tenant_id, state.session_id)
        async with self._lock:
            if key in self._states:
                raise ConflictError(f"context state already exists: {state.session_id}")
            self._states[key] = state

    async def get_state(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextState:
        value = self._states.get((tenant_id, session_id))
        if value is None or value.owner_user_id != owner_user_id:
            raise NotFoundError(f"context state not found: {session_id}")
        return value

    async def compare_and_set_state(
        self,
        expected_revision: int,
        state: SessionContextState,
    ) -> bool:
        key = (state.tenant_id, state.session_id)
        async with self._lock:
            current = self._states.get(key)
            if current is None or current.owner_user_id != state.owner_user_id:
                raise NotFoundError(f"context state not found: {state.session_id}")
            if current.revision != expected_revision:
                return False
            validate_state_update(expected_revision, current, state)
            self._states[key] = state
            return True

    async def publish_digest(
        self,
        expected_state_revision: int,
        state: SessionContextState,
        digest: SessionContextDigest,
    ) -> bool:
        key = (state.tenant_id, state.session_id)
        async with self._lock:
            current = self._states.get(key)
            if current is None or current.owner_user_id != state.owner_user_id:
                raise NotFoundError(f"context state not found: {state.session_id}")
            if current.revision != expected_state_revision:
                return False
            validate_digest_publication(expected_state_revision, current, state, digest)
            digest_key = (digest.tenant_id, digest.session_id, digest.version)
            if digest_key in self._digests:
                raise ConflictError(f"context digest already exists: {digest.digest_id}")
            self._digests[digest_key] = digest
            self._states[key] = state
            return True

    async def get_digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        digest_id: str,
    ) -> SessionContextDigest:
        values = (
            value
            for (item_tenant, item_session, _), value in self._digests.items()
            if item_tenant == tenant_id
            and item_session == session_id
            and value.digest_id == digest_id
            and value.owner_user_id == owner_user_id
        )
        value = next(values, None)
        if value is None:
            raise NotFoundError(f"context digest not found: {digest_id}")
        return value

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
        values = [
            value
            for (item_tenant, item_session, version), value in self._digests.items()
            if item_tenant == tenant_id
            and item_session == session_id
            and value.owner_user_id == owner_user_id
            and (before_version is None or version < before_version)
        ]
        return sorted(values, key=lambda value: value.version, reverse=True)[:limit]
