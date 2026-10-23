"""Portable archive and restore for session workspaces."""

import asyncio
import hashlib
import os
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from gzip import BadGzipFile
from io import BytesIO
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from harness.core.models import WorkspaceSnapshot
from harness.core.ports import ArtifactStore, SessionRepository, WorkspaceSnapshotRepository
from harness.quota.models import QuotaResource, ResourceReservation
from harness.quota.service import QuotaService

_TRANSIENT_ROOTS = frozenset(
    {".claude", ".git", ".harness-runtime", ".tmp", "inputs"}
)
_DEPENDENCY_DIRECTORIES = frozenset(
    {
        ".cache",
        ".npm",
        ".pnpm-store",
        ".venv",
        ".yarn",
        "__pycache__",
        "node_modules",
        "vendor",
        "venv",
    }
)


@dataclass(frozen=True)
class WorkspacePolicy:
    restore_session: bool = True
    archive_on_complete: bool = True


class WorkspacePolicyResolver(Protocol):
    async def __call__(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> WorkspacePolicy: ...


class WorkspaceService:
    def __init__(
        self,
        store: ArtifactStore,
        *,
        snapshots: WorkspaceSnapshotRepository | None = None,
        max_archive_bytes: int = 512 * 1024 * 1024,
        max_archive_members: int = 10_000,
        sessions: SessionRepository | None = None,
        quotas: QuotaService | None = None,
    ) -> None:
        if max_archive_bytes <= 0 or max_archive_members <= 0:
            raise ValueError("workspace archive limits must be positive")
        self._store = store
        self._snapshots = snapshots
        self._max_archive_bytes = max_archive_bytes
        self._max_archive_members = max_archive_members
        self._sessions = sessions
        self._quotas = quotas

    async def archive(
        self, *, tenant_id: str, session_id: str, workspace: Path
    ) -> WorkspaceSnapshot:
        snapshot_id = f"snapshot_{uuid4().hex}"

        def pack() -> bytes:
            paths: list[Path] = []
            total_size = 0

            def retain(path: Path) -> None:
                nonlocal total_size
                if len(paths) >= self._max_archive_members:
                    raise ValueError("workspace archive exceeds member limit")
                if path.is_symlink():
                    raise ValueError("workspace contains an unsafe symlink")
                if not (path.is_dir() or path.is_file()):
                    raise ValueError("workspace contains an unsupported file type")
                if path.is_file():
                    total_size += path.stat().st_size
                    if total_size > self._max_archive_bytes:
                        raise ValueError("workspace archive exceeds size limit")
                paths.append(path)

            # Runtime assets, immutable inputs and package caches are staged
            # independently on every Run. Archiving them bloats a conversational
            # workspace and can make a single npm install exhaust the member
            # limit even though the user's files are small.
            for root, directory_names, file_names in os.walk(
                workspace,
                topdown=True,
                followlinks=False,
            ):
                root_path = Path(root)
                retained_directories: list[str] = []
                for name in sorted(directory_names):
                    path = root_path / name
                    if path.is_symlink():
                        raise ValueError("workspace contains an unsafe symlink")
                    relative = path.relative_to(workspace)
                    if (
                        relative.parts[0] in _TRANSIENT_ROOTS
                        or name in _DEPENDENCY_DIRECTORIES
                    ):
                        continue
                    retain(path)
                    retained_directories.append(name)
                directory_names[:] = retained_directories
                for name in sorted(file_names):
                    retain(root_path / name)
            buffer = BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for path in sorted(paths):
                    archive.add(
                        path,
                        arcname=path.relative_to(workspace),
                        recursive=False,
                    )
            content = buffer.getvalue()
            if len(content) > self._max_archive_bytes:
                raise ValueError("compressed workspace archive exceeds size limit")
            return content

        content = await asyncio.to_thread(pack)
        return await self._persist_archive(tenant_id, session_id, snapshot_id, content)

    async def fork_latest(
        self, *, tenant_id: str, source_session_id: str, target_session_id: str
    ) -> WorkspaceSnapshot | None:
        """Copy the frozen archive, so retention or later source writes cannot alter a branch."""
        if self._snapshots is None:
            return None
        source = await self._snapshots.latest(tenant_id, source_session_id)
        if source is None:
            return None
        content = await self._store.get(tenant_id, source.snapshot_id)
        if hashlib.sha256(content).hexdigest() != source.sha256:
            raise ValueError("workspace snapshot hash mismatch")
        return await self._persist_archive(
            tenant_id, target_session_id, f"snapshot_{uuid4().hex}", content
        )

    async def _persist_archive(
        self, tenant_id: str, session_id: str, snapshot_id: str, content: bytes
    ) -> WorkspaceSnapshot:
        reservation: ResourceReservation | None = None
        if self._quotas is not None:
            session = (
                await self._sessions.get(tenant_id, session_id)
                if self._sessions is not None
                else None
            )
            reservation = await self._quotas.reserve(
                tenant_id=tenant_id,
                resource=QuotaResource.SNAPSHOT_BYTES,
                amount=max(1, len(content)),
                subject_id=snapshot_id,
                idempotency_key=f"snapshot:{snapshot_id}:bytes",
                agent_name=session.agent_name if session is not None else None,
                environment=session.environment if session is not None else None,
            )
        try:
            stored = await self._store.put(tenant_id, snapshot_id, content)
        except Exception:
            if reservation is not None and self._quotas is not None:
                await self._quotas.release(reservation)
            raise
        snapshot = WorkspaceSnapshot(
            snapshot_id=snapshot_id,
            session_id=session_id,
            tenant_id=tenant_id,
            object_key=stored.object_key,
            sha256=stored.sha256,
            created_at=datetime.now(UTC),
        )
        try:
            if self._snapshots is not None:
                await self._snapshots.add(snapshot)
        except Exception:
            if reservation is not None and self._quotas is not None:
                await self._quotas.release(reservation)
            raise
        if reservation is not None and self._quotas is not None:
            await self._quotas.commit(reservation, amount=len(content))
        return snapshot

    async def restore_latest(
        self, *, tenant_id: str, session_id: str, workspace: Path
    ) -> WorkspaceSnapshot | None:
        if self._snapshots is None:
            return None
        snapshot = await self._snapshots.latest(tenant_id, session_id)
        if snapshot is None:
            return None
        await self.restore(snapshot, workspace=workspace)
        return snapshot

    async def restore(self, snapshot: WorkspaceSnapshot, *, workspace: Path) -> None:
        content = await self._store.get(snapshot.tenant_id, snapshot.snapshot_id)
        if hashlib.sha256(content).hexdigest() != snapshot.sha256:
            raise ValueError("workspace snapshot hash mismatch")

        def unpack() -> None:
            workspace.mkdir(parents=True, exist_ok=True)
            root = workspace.resolve()
            try:
                archive = tarfile.open(fileobj=BytesIO(content), mode="r:gz")
            except (tarfile.TarError, BadGzipFile, OSError, EOFError) as error:
                raise ValueError("invalid workspace archive") from error
            with archive:
                members = archive.getmembers()
                total_size = 0
                for member in archive.getmembers():
                    normalized_name = member.name.replace("\\", "/")
                    relative = Path(normalized_name)
                    target = (root / relative).resolve()
                    if (
                        relative.is_absolute()
                        or ".." in relative.parts
                        or not target.is_relative_to(root)
                        or member.issym()
                        or member.islnk()
                        or not (member.isdir() or member.isreg())
                    ):
                        raise ValueError("workspace archive contains an unsafe path")
                    total_size += member.size
                    if total_size > self._max_archive_bytes:
                        raise ValueError("workspace archive exceeds extraction limit")
                for member in members:
                    target = root / Path(member.name.replace("\\", "/"))
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise ValueError("invalid workspace archive")
                    target.write_bytes(source.read())
                    target.chmod(member.mode & 0o755)

        await asyncio.to_thread(unpack)
