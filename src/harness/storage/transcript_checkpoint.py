"""Canonical PostgreSQL checkpoint over opaque Claude SDK transcript entries."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from harness.context.checkpoint import TranscriptCheckpoint, sdk_session_id_hash
from harness.storage.database import SessionFactory
from harness.storage.models import SdkSessionEntryRow


class PostgresTranscriptCheckpointProvider:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def checkpoint(
        self,
        tenant_id: str,
        project_id: str,
        sdk_session_id: str,
    ) -> TranscriptCheckpoint | None:
        statement = (
            select(
                SdkSessionEntryRow.subpath,
                SdkSessionEntryRow.sequence,
                SdkSessionEntryRow.payload,
            )
            .where(
                SdkSessionEntryRow.tenant_id == tenant_id,
                SdkSessionEntryRow.project_id == project_id,
                SdkSessionEntryRow.session_id == sdk_session_id,
            )
            .order_by(SdkSessionEntryRow.subpath, SdkSessionEntryRow.sequence)
        )
        async with self._sessions() as db:
            rows = (await db.execute(statement)).all()
        if not rows:
            return None
        digest = hashlib.sha256()
        for subpath, sequence, payload in rows:
            encoded = json.dumps(
                {
                    "payload": payload,
                    "sequence": sequence,
                    "subpath": subpath,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        return TranscriptCheckpoint(
            sdk_session_id_hash=sdk_session_id_hash(sdk_session_id),
            transcript_checkpoint_hash=f"sha256:{digest.hexdigest()}",
            entry_count=len(rows),
        )
