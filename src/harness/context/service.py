"""Monotonic Session trust and content-addressed Digest publication."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import datetime

from harness.context.models import (
    ContextDigestCreator,
    ContextDigestEntry,
    ContextDigestObjectRef,
    ContextDigestSource,
    SessionContextDigest,
    SessionContextOverview,
    SessionContextState,
    context_digest_content_hash,
)
from harness.context.repositories import ContextRepository
from harness.core.errors import ConflictError, NotFoundError
from harness.policy.models import ContextTrust
from harness.policy.results import stricter_trust
from harness.runtime.audit_redaction import redact_text

CONTEXT_REBASE_ROUTE_ID = "context-rebase-v1"
CONTEXT_EVENT_PROJECTION_ROUTE_ID = "event-projection-v1"


class ContextService:
    def __init__(
        self,
        repository: ContextRepository,
        *,
        clock: Callable[[], datetime],
        id_generator: Callable[[str], str],
        max_cas_attempts: int = 8,
    ) -> None:
        if max_cas_attempts <= 0:
            raise ValueError("context CAS attempts must be positive")
        self._repository = repository
        self._clock = clock
        self._id_generator = id_generator
        self._max_cas_attempts = max_cas_attempts

    async def state(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextState:
        try:
            return await self._repository.get_state(
                tenant_id,
                owner_user_id,
                session_id,
            )
        except NotFoundError:
            initial = SessionContextState(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                session_id=session_id,
                revision=1,
                updated_at=self._clock(),
            )
            try:
                await self._repository.add_state(initial)
                return initial
            except ConflictError:
                return await self._repository.get_state(
                    tenant_id,
                    owner_user_id,
                    session_id,
                )

    async def promote_trust(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        trust: ContextTrust,
    ) -> SessionContextState:
        for _ in range(self._max_cas_attempts):
            current = await self.state(tenant_id, owner_user_id, session_id)
            promoted = stricter_trust(current.trust_high_watermark, trust)
            if promoted == current.trust_high_watermark:
                return current
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "trust_high_watermark": promoted,
                    "updated_at": self._clock(),
                }
            )
            if await self._repository.compare_and_set_state(current.revision, updated):
                return updated
        raise ConflictError("context trust changed too frequently")

    async def create_digest(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        source: ContextDigestSource,
        created_by: ContextDigestCreator,
        facts: Sequence[ContextDigestEntry] = (),
        decisions: Sequence[ContextDigestEntry] = (),
        open_tasks: Sequence[ContextDigestEntry] = (),
        artifact_refs: Sequence[ContextDigestObjectRef] = (),
        workspace_refs: Sequence[ContextDigestObjectRef] = (),
    ) -> SessionContextDigest:
        safe_facts = tuple(self._sanitize_entry(value) for value in facts)
        safe_decisions = tuple(self._sanitize_entry(value) for value in decisions)
        safe_open_tasks = tuple(self._sanitize_entry(value) for value in open_tasks)
        entry_trust = ContextTrust.SAFE
        for value in (*safe_facts, *safe_decisions, *safe_open_tasks):
            entry_trust = stricter_trust(entry_trust, value.trust)
        await self.promote_trust(
            tenant_id,
            owner_user_id,
            session_id,
            entry_trust,
        )
        for _ in range(self._max_cas_attempts):
            current = await self.state(tenant_id, owner_user_id, session_id)
            if current.transcript_checkpoint_hash == source.transcript_checkpoint_hash:
                existing = await self._repository.latest_digest(
                    tenant_id,
                    owner_user_id,
                    session_id,
                )
                if existing is None:
                    raise ConflictError("context state has a missing Digest pointer")
                return existing
            digest_id = self._id_generator("context_digest")
            payload = {
                "schema_version": 1,
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "session_id": session_id,
                "digest_id": digest_id,
                "version": current.latest_digest_version + 1,
                "source": source.model_dump(mode="json"),
                "trust_high_watermark": current.trust_high_watermark.value,
                "facts": [value.model_dump(mode="json") for value in safe_facts],
                "decisions": [value.model_dump(mode="json") for value in safe_decisions],
                "open_tasks": [value.model_dump(mode="json") for value in safe_open_tasks],
                "artifact_refs": [value.model_dump(mode="json") for value in artifact_refs],
                "workspace_refs": [value.model_dump(mode="json") for value in workspace_refs],
                "created_by": created_by.model_dump(mode="json"),
                "created_at": self._clock(),
            }
            digest = SessionContextDigest.model_validate(
                {**payload, "content_hash": context_digest_content_hash(payload)}
            )
            updated = current.model_copy(
                update={
                    "revision": current.revision + 1,
                    "latest_digest_id": digest.digest_id,
                    "latest_digest_version": digest.version,
                    "transcript_checkpoint_hash": source.transcript_checkpoint_hash,
                    "updated_at": self._clock(),
                }
            )
            if await self._repository.publish_digest(current.revision, updated, digest):
                return digest
        raise ConflictError("context Digest changed too frequently")

    async def latest_digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> SessionContextDigest | None:
        try:
            return await self._repository.latest_digest(
                tenant_id,
                owner_user_id,
                session_id,
            )
        except NotFoundError:
            # A normal fresh Session has no context state yet. Reads are
            # intentionally fail-open so recovery support never blocks its
            # first Run.
            return None

    async def digest(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        digest_id: str,
    ) -> SessionContextDigest:
        return await self._repository.get_digest(
            tenant_id,
            owner_user_id,
            session_id,
            digest_id,
        )

    async def overview(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
        *,
        before_version: int | None = None,
        limit: int = 20,
    ) -> SessionContextOverview:
        if not 1 <= limit <= 50:
            raise ValueError("context Digest page size must be between 1 and 50")
        try:
            state = await self._repository.get_state(
                tenant_id,
                owner_user_id,
                session_id,
            )
        except NotFoundError:
            return SessionContextOverview(session_id=session_id)
        values = await self._repository.list_digests(
            tenant_id,
            owner_user_id,
            session_id,
            before_version=before_version,
            limit=limit + 1,
        )
        has_more = len(values) > limit
        page = tuple(values[:limit])
        return SessionContextOverview(
            session_id=session_id,
            state=state,
            digests=page,
            next_before_version=(page[-1].version if has_more and page else None),
        )

    async def create_rebase_digest(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        source_session_id: str,
        target_session_id: str,
    ) -> SessionContextDigest:
        source = await self.latest_digest(
            tenant_id,
            owner_user_id,
            source_session_id,
        )
        if source is None:
            raise ConflictError("context rebase requires a durable Digest")
        return await self.create_digest(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            session_id=target_session_id,
            source=source.source,
            created_by=ContextDigestCreator(
                route_id=CONTEXT_REBASE_ROUTE_ID,
                model="deterministic",
                prompt_revision="context-rebase-v1",
            ),
            facts=source.facts,
            decisions=source.decisions,
            open_tasks=source.open_tasks,
            artifact_refs=source.artifact_refs,
            workspace_refs=source.workspace_refs,
        )

    async def recovery_projection(
        self,
        tenant_id: str,
        owner_user_id: str,
        session_id: str,
    ) -> str:
        digest = await self.latest_digest(tenant_id, owner_user_id, session_id)
        if digest is None or digest.created_by.route_id not in {
            CONTEXT_REBASE_ROUTE_ID,
            CONTEXT_EVENT_PROJECTION_ROUTE_ID,
        }:
            return ""
        payload = {
            "schema_version": digest.schema_version,
            "digest_id": digest.digest_id,
            "trust_high_watermark": digest.trust_high_watermark.value,
            "facts": [value.model_dump(mode="json") for value in digest.facts[:20]],
            "decisions": [value.model_dump(mode="json") for value in digest.decisions[:20]],
            "open_tasks": [value.model_dump(mode="json") for value in digest.open_tasks[:20]],
            "artifact_refs": [value.model_dump(mode="json") for value in digest.artifact_refs[:20]],
            "workspace_refs": [
                value.model_dump(mode="json") for value in digest.workspace_refs[:10]
            ],
            "source": {
                "through_run_id": digest.source.through_run_id,
                "through_event_sequence": digest.source.through_event_sequence,
                "transcript_checkpoint_hash": (digest.source.transcript_checkpoint_hash),
            },
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        # Keep the wrapper structurally unambiguous even when an untrusted fact
        # contains tag-like text. JSON parsers and models still recover the text.
        encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        return (
            f'<context_recovery_data schema="1" '
            f'trust="{digest.trust_high_watermark.value}">\n'
            f"{encoded}\n"
            "</context_recovery_data>"
        )

    @staticmethod
    def _sanitize_entry(value: ContextDigestEntry) -> ContextDigestEntry:
        return value.model_copy(update={"text": redact_text(value.text, limit=1_000)})
