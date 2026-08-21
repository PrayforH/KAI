"""Project durable Run events and SDK transcript state into a recovery Digest."""

from __future__ import annotations

import hashlib
from typing import Protocol

from pydantic import Field

from harness.context.models import (
    ContextDigestCreator,
    ContextDigestEntry,
    ContextDigestObjectRef,
    ContextDigestSource,
    ContextModel,
    SessionContextDigest,
)
from harness.context.service import ContextService
from harness.core.events import RunEvent
from harness.core.models import Run, Session


class TranscriptCheckpoint(ContextModel):
    sdk_session_id_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    transcript_checkpoint_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    entry_count: int = Field(ge=1)


class TranscriptCheckpointProvider(Protocol):
    async def checkpoint(
        self,
        tenant_id: str,
        project_id: str,
        sdk_session_id: str,
    ) -> TranscriptCheckpoint | None: ...


def _content_hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("sha256:").lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        return None
    return f"sha256:{normalized}"


class ContextCheckpointService:
    def __init__(
        self,
        contexts: ContextService,
        transcripts: TranscriptCheckpointProvider,
    ) -> None:
        self._contexts = contexts
        self._transcripts = transcripts

    async def checkpoint_run(
        self,
        *,
        session: Session,
        run: Run,
        events: list[RunEvent],
        final_response: str,
    ) -> SessionContextDigest | None:
        if session.runtime_type != "claude-agent-sdk":
            return None
        sdk_session_id = session.resolved_runtime_thread_id
        if sdk_session_id is None:
            return None
        checkpoint = await self._transcripts.checkpoint(
            run.tenant_id,
            run.session_id,
            sdk_session_id,
        )
        if checkpoint is None:
            return None
        through_sequence = max((event.sequence for event in events), default=0)
        state = await self._contexts.state(
            run.tenant_id,
            session.user_id,
            run.session_id,
        )
        final_event = next(
            (event for event in reversed(events) if event.type == "message.completed"),
            None,
        )
        facts = (
            (
                ContextDigestEntry(
                    text=final_response[:1_000],
                    source_refs=(
                        "run:"
                        f"{run.run_id}:event:"
                        f"{final_event.sequence if final_event else through_sequence}",
                    ),
                    trust=state.trust_high_watermark,
                ),
            )
            if final_response.strip()
            else ()
        )
        artifact_refs = tuple(
            ContextDigestObjectRef(
                ref=f"artifact:{event.payload['artifact_id']}",
                content_hash=content_hash,
                title=str(event.payload.get("name") or event.payload["artifact_id"]),
                media_type=(
                    str(event.payload["media_type"]) if event.payload.get("media_type") else None
                ),
            )
            for event in events
            if event.type == "artifact.ready"
            and isinstance(event.payload.get("artifact_id"), str)
            and (content_hash := _content_hash(event.payload.get("sha256"))) is not None
        )
        workspace_refs = tuple(
            ContextDigestObjectRef(
                ref=f"workspace:{event.payload['snapshot_id']}",
                content_hash=content_hash,
                title="Session workspace snapshot",
                media_type="application/gzip",
            )
            for event in events
            if event.type == "workspace.archived"
            and isinstance(event.payload.get("snapshot_id"), str)
            and (content_hash := _content_hash(event.payload.get("sha256"))) is not None
        )
        return await self._contexts.create_digest(
            tenant_id=run.tenant_id,
            owner_user_id=session.user_id,
            session_id=run.session_id,
            source=ContextDigestSource(
                sdk_session_id_hash=checkpoint.sdk_session_id_hash,
                through_run_id=run.run_id,
                through_event_sequence=through_sequence,
                transcript_checkpoint_hash=checkpoint.transcript_checkpoint_hash,
            ),
            facts=facts,
            artifact_refs=artifact_refs,
            workspace_refs=workspace_refs,
            created_by=ContextDigestCreator(
                route_id="event-projection-v1",
                model="deterministic-event-projection",
                prompt_revision="p1.2-v1",
            ),
        )


def sdk_session_id_hash(sdk_session_id: str) -> str:
    return f"sha256:{hashlib.sha256(sdk_session_id.encode()).hexdigest()}"
