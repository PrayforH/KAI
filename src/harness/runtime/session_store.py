"""PostgreSQL transcript mirror for Claude Agent SDK session resume."""

from datetime import UTC, datetime
from typing import cast

from claude_agent_sdk import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStoreEntry,
    SessionStoreListEntry,
)
from sqlalchemy import delete, func, select, text

from harness.storage.database import SessionFactory
from harness.storage.models import SdkSessionEntryRow


def _subpath(key: SessionKey | SessionListSubkeysKey) -> str:
    return str(key.get("subpath", ""))


class PostgresSessionStore:
    """Tenant-bound SDK SessionStore with opaque JSON transcript entries."""

    def __init__(
        self,
        sessions: SessionFactory,
        *,
        tenant_id: str,
        project_id: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._tenant_id = tenant_id
        # Claude derives project_key from cwd. Harness workspaces have a new
        # temporary path for every Run, so using that key would make the next
        # Run unable to find the previous transcript. A Harness Session-bound
        # project id keeps resume stable across sandboxes, workers and hosts.
        self._project_id = project_id

    def _project(self, key: SessionKey | SessionListSubkeysKey) -> str:
        return self._project_id or key["project_key"]

    async def revision(self) -> str:
        """Watermark all transcripts, including child sessions and deletion/rebase.

        Checked under the Session gate before borrowing a local warm connection.
        A different Worker may have advanced the same native session ID.
        """
        if self._project_id is None:
            raise ValueError("Warm SDK reuse requires a Session-bound store")
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        func.count(),
                        func.max(SdkSessionEntryRow.modified_at),
                        func.sum(SdkSessionEntryRow.sequence),
                        func.max(SdkSessionEntryRow.entry_id),
                    ).where(
                        SdkSessionEntryRow.tenant_id == self._tenant_id,
                        SdkSessionEntryRow.project_id == self._project_id,
                    )
                )
            ).one()
            return f"{row[0]}:{row[1]}:{row[2]}:{row[3]}"

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        if not entries:
            return
        subpath = _subpath(key)
        project_id = self._project(key)
        lock_key = f"{self._tenant_id}:{project_id}:{key['session_id']}:{subpath}"
        async with self._sessions() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            maximum = await session.scalar(
                select(func.max(SdkSessionEntryRow.sequence)).where(
                    SdkSessionEntryRow.tenant_id == self._tenant_id,
                    SdkSessionEntryRow.project_id == project_id,
                    SdkSessionEntryRow.session_id == key["session_id"],
                    SdkSessionEntryRow.subpath == subpath,
                )
            )
            next_sequence = int(maximum or 0) + 1
            uuids = {str(value) for entry in entries if (value := entry.get("uuid"))}
            existing: set[str] = set()
            if uuids:
                found = (
                    await session.scalars(
                        select(SdkSessionEntryRow.entry_uuid).where(
                            SdkSessionEntryRow.tenant_id == self._tenant_id,
                            SdkSessionEntryRow.project_id == project_id,
                            SdkSessionEntryRow.session_id == key["session_id"],
                            SdkSessionEntryRow.subpath == subpath,
                            SdkSessionEntryRow.entry_uuid.in_(uuids),
                        )
                    )
                ).all()
                existing = {value for value in found if value is not None}
            now = datetime.now(UTC)
            for entry in entries:
                value = entry.get("uuid")
                entry_uuid = str(value) if value is not None else None
                if entry_uuid is not None and entry_uuid in existing:
                    continue
                session.add(
                    SdkSessionEntryRow(
                        tenant_id=self._tenant_id,
                        project_id=project_id,
                        session_id=key["session_id"],
                        subpath=subpath,
                        sequence=next_sequence,
                        entry_uuid=entry_uuid,
                        modified_at=now,
                        payload=dict(entry),
                    )
                )
                next_sequence += 1
            await session.commit()

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        project_id = self._project(key)
        statement = (
            select(SdkSessionEntryRow.payload)
            .where(
                SdkSessionEntryRow.tenant_id == self._tenant_id,
                SdkSessionEntryRow.project_id == project_id,
                SdkSessionEntryRow.session_id == key["session_id"],
                SdkSessionEntryRow.subpath == _subpath(key),
            )
            .order_by(SdkSessionEntryRow.sequence)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
            if not rows:
                return None
            return [cast(SessionStoreEntry, dict(row)) for row in rows]

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        project_id = self._project_id or project_key
        statement = (
            select(
                SdkSessionEntryRow.session_id,
                func.max(SdkSessionEntryRow.modified_at),
            )
            .where(
                SdkSessionEntryRow.tenant_id == self._tenant_id,
                SdkSessionEntryRow.project_id == project_id,
                SdkSessionEntryRow.subpath == "",
            )
            .group_by(SdkSessionEntryRow.session_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            return [
                SessionStoreListEntry(
                    session_id=session_id,
                    mtime=modified_at.timestamp() * 1000,
                )
                for session_id, modified_at in rows
            ]

    async def delete(self, key: SessionKey) -> None:
        conditions = [
            SdkSessionEntryRow.tenant_id == self._tenant_id,
            SdkSessionEntryRow.project_id == self._project(key),
            SdkSessionEntryRow.session_id == key["session_id"],
        ]
        if key.get("subpath") is not None:
            conditions.append(SdkSessionEntryRow.subpath == _subpath(key))
        async with self._sessions() as session:
            await session.execute(delete(SdkSessionEntryRow).where(*conditions))
            await session.commit()

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        statement = (
            select(SdkSessionEntryRow.subpath)
            .where(
                SdkSessionEntryRow.tenant_id == self._tenant_id,
                SdkSessionEntryRow.project_id == self._project(key),
                SdkSessionEntryRow.session_id == key["session_id"],
                SdkSessionEntryRow.subpath != "",
            )
            .distinct()
            .order_by(SdkSessionEntryRow.subpath)
        )
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())
