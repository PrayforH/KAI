"""PostgreSQL repositories for durable platform state beyond runs and events."""

from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import (
    AgentRuntimeType,
    AgentVersion,
    AguiThreadBinding,
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    InputArtifact,
    Session,
    ThreadFile,
    UserMemory,
    WorkspaceSnapshot,
)
from harness.storage.database import SessionFactory
from harness.storage.models import (
    AgentVersionRow,
    AguiThreadBindingRow,
    ApprovalRow,
    ArtifactRow,
    InputArtifactRow,
    SessionRow,
    ThreadFileRow,
    UserMemoryRow,
    WorkspaceSnapshotRow,
)


async def _commit_add(session: Any, *, message: str) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(message) from error


class PostgresAgentRegistry:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, version: AgentVersion) -> None:
        async with self._sessions() as session:
            session.add(
                AgentVersionRow(
                    tenant_id=version.tenant_id,
                    owner_user_id=version.owner_user_id,
                    name=version.name,
                    version=version.version,
                    agent_id=version.agent_id,
                    status=version.status.value,
                    manifest_hash=version.manifest_hash,
                    package_hash=version.package_hash,
                    created_at=version.created_at,
                    catalog_manifest=(
                        version.snapshot.get("manifest", {})
                        if isinstance(version.snapshot.get("manifest"), dict)
                        else {}
                    ),
                    payload=version.model_dump(mode="json"),
                )
            )
            await _commit_add(
                session,
                message=f"agent version already exists: {version.name}@{version.version}",
            )

    async def get(
        self, tenant_id: str, owner_user_id: str, name: str, version: str
    ) -> AgentVersion:
        async with self._sessions() as session:
            row = await session.get(AgentVersionRow, (tenant_id, owner_user_id, name, version))
            if row is None:
                raise NotFoundError(f"agent version not found: {name}@{version}")
            loaded = AgentVersion.model_validate(row.payload)
            if loaded.agent_id is None and row.agent_id is not None:
                # Legacy rows backfilled by migration 0023 carry the identity in
                # the envelope column only.
                loaded = loaded.model_copy(update={"agent_id": row.agent_id})
            return loaded

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        statement = (
            select(AgentVersionRow.payload, AgentVersionRow.agent_id)
            .where(
                AgentVersionRow.tenant_id == tenant_id,
                AgentVersionRow.owner_user_id == owner_user_id,
            )
            .order_by(AgentVersionRow.name, AgentVersionRow.version)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            result: list[AgentVersion] = []
            for payload, envelope_agent_id in rows:
                loaded = AgentVersion.model_validate(payload)
                if loaded.agent_id is None and envelope_agent_id is not None:
                    loaded = loaded.model_copy(update={"agent_id": envelope_agent_id})
                result.append(loaded)
            return result

    async def list_catalog_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        """Load the task-catalog projection without transferring package files.

        Published versions may embed large reproducible assets under
        ``snapshot.files``. Navigation only needs the manifest, so selecting
        JSON fields server-side prevents a single historical release from
        turning every task-page transition into a multi-megabyte read.
        """
        statement = (
            select(
                AgentVersionRow.name,
                AgentVersionRow.version,
                AgentVersionRow.agent_id,
                AgentVersionRow.status,
                AgentVersionRow.manifest_hash,
                AgentVersionRow.package_hash,
                AgentVersionRow.created_at,
                AgentVersionRow.catalog_manifest,
            )
            .where(
                AgentVersionRow.tenant_id == tenant_id,
                AgentVersionRow.owner_user_id == owner_user_id,
            )
            .order_by(AgentVersionRow.name, AgentVersionRow.version)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            return [
                AgentVersion(
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    name=name,
                    version=version,
                    agent_id=agent_id,
                    status=status,
                    manifest_hash=manifest_hash,
                    package_hash=package_hash,
                    created_at=created_at,
                    snapshot={"manifest": manifest or {}},
                )
                for (
                    name,
                    version,
                    agent_id,
                    status,
                    manifest_hash,
                    package_hash,
                    created_at,
                    manifest,
                ) in rows
            ]

    async def move_owner(
        self, tenant_id: str, from_user_id: str, to_user_id: str, name: str
    ) -> int:
        if from_user_id == to_user_id:
            return 0
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentVersionRow)
                    .where(
                        AgentVersionRow.tenant_id == tenant_id,
                        AgentVersionRow.owner_user_id == from_user_id,
                        AgentVersionRow.name == name,
                    )
                    .with_for_update()
                )
            ).all()
            for row in rows:
                conflict = await session.get(
                    AgentVersionRow, (tenant_id, to_user_id, name, row.version)
                )
                if conflict is not None:
                    raise ConflictError(
                        f"target user already owns an Agent version: {name}@{row.version}"
                    )
            for row in rows:
                payload = dict(row.payload)
                payload["owner_user_id"] = to_user_id
                session.add(
                    AgentVersionRow(
                        tenant_id=tenant_id,
                        owner_user_id=to_user_id,
                        name=name,
                        version=row.version,
                        agent_id=row.agent_id,
                        payload=payload,
                    )
                )
                await session.delete(row)
            await session.commit()
            return len(rows)


class PostgresSessionRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, session: Session) -> None:
        async with self._sessions() as db_session:
            db_session.add(
                SessionRow(
                    tenant_id=session.tenant_id,
                    session_id=session.session_id,
                    user_id=session.user_id,
                    payload=session.model_dump(mode="json"),
                )
            )
            await _commit_add(db_session, message=f"session already exists: {session.session_id}")

    async def get(self, tenant_id: str, session_id: str) -> Session:
        async with self._sessions() as session:
            row = await session.get(SessionRow, (tenant_id, session_id))
            if row is None:
                raise NotFoundError(f"session not found: {session_id}")
            return Session.model_validate(row.payload)

    async def list_for_ids(self, tenant_id: str, session_ids: list[str]) -> list[Session]:
        if not session_ids:
            return []
        wanted = list(dict.fromkeys(session_ids))
        statement = select(SessionRow.payload).where(
            SessionRow.tenant_id == tenant_id,
            SessionRow.session_id.in_(wanted),
        )
        async with self._sessions() as session:
            payloads = (await session.execute(statement)).scalars().all()
        found = {
            item.session_id: item
            for payload in payloads
            for item in (Session.model_validate(payload),)
        }
        for session_id in wanted:
            if session_id not in found:
                raise NotFoundError(f"session not found: {session_id}")
        return [found[session_id] for session_id in session_ids]

    async def bind_claude_session_id(
        self, tenant_id: str, session_id: str, claude_session_id: str
    ) -> Session:
        return await self.bind_runtime_thread(
            tenant_id,
            session_id,
            "claude-agent-sdk",
            claude_session_id,
        )

    async def bind_runtime_thread(
        self,
        tenant_id: str,
        session_id: str,
        runtime_type: AgentRuntimeType,
        runtime_thread_id: str,
    ) -> Session:
        if not runtime_thread_id:
            raise ValueError("runtime_thread_id must be non-empty")
        async with self._sessions() as session:
            row = await session.get(
                SessionRow,
                (tenant_id, session_id),
                with_for_update=True,
            )
            if row is None:
                raise NotFoundError(f"session not found: {session_id}")
            current = Session.model_validate(row.payload)
            if current.runtime_type != runtime_type:
                raise ConflictError(
                    f"session {session_id} is pinned to runtime {current.runtime_type}"
                )
            current_thread_id = current.resolved_runtime_thread_id
            if current_thread_id is not None:
                if current_thread_id != runtime_thread_id:
                    raise ConflictError(
                        f"session {session_id} is already bound to another runtime thread"
                    )
                return current
            update: dict[str, str] = {"runtime_thread_id": runtime_thread_id}
            if runtime_type == "claude-agent-sdk":
                update["claude_session_id"] = runtime_thread_id
            updated = current.model_copy(update=update)
            row.payload = updated.model_dump(mode="json")
            await session.commit()
            return updated

    async def clear_claude_session_id(
        self, tenant_id: str, session_id: str, expected_claude_session_id: str
    ) -> Session:
        return await self.clear_runtime_thread(
            tenant_id,
            session_id,
            "claude-agent-sdk",
            expected_claude_session_id,
        )

    async def clear_runtime_thread(
        self,
        tenant_id: str,
        session_id: str,
        runtime_type: AgentRuntimeType,
        expected_runtime_thread_id: str,
    ) -> Session:
        async with self._sessions() as session:
            row = await session.get(
                SessionRow,
                (tenant_id, session_id),
                with_for_update=True,
            )
            if row is None:
                raise NotFoundError(f"session not found: {session_id}")
            current = Session.model_validate(row.payload)
            if current.runtime_type != runtime_type:
                raise ConflictError(
                    f"session {session_id} is pinned to runtime {current.runtime_type}"
                )
            if current.resolved_runtime_thread_id != expected_runtime_thread_id:
                raise ConflictError(f"session {session_id} runtime thread changed during recovery")
            update: dict[str, None] = {"runtime_thread_id": None}
            if runtime_type == "claude-agent-sdk":
                update["claude_session_id"] = None
            updated = current.model_copy(update=update)
            row.payload = updated.model_dump(mode="json")
            await session.commit()
            return updated


class PostgresApprovalRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, approval: ApprovalRequest) -> None:
        async with self._sessions() as session:
            session.add(
                ApprovalRow(
                    tenant_id=approval.tenant_id,
                    approval_id=approval.approval_id,
                    run_id=approval.run_id,
                    tool_call_id=approval.tool_call_id,
                    status=approval.status.value,
                    expires_at=approval.expires_at,
                    payload=approval.model_dump(mode="json"),
                )
            )
            await _commit_add(session, message=f"approval already exists: {approval.approval_id}")

    async def get(self, tenant_id: str, approval_id: str) -> ApprovalRequest:
        async with self._sessions() as session:
            row = await session.get(ApprovalRow, (tenant_id, approval_id))
            if row is None:
                raise NotFoundError(f"approval not found: {approval_id}")
            return ApprovalRequest.model_validate(row.payload)

    async def find_by_tool_call(
        self, tenant_id: str, run_id: str, tool_call_id: str
    ) -> ApprovalRequest | None:
        statement = select(ApprovalRow.payload).where(
            ApprovalRow.tenant_id == tenant_id,
            ApprovalRow.run_id == run_id,
            ApprovalRow.tool_call_id == tool_call_id,
        )
        async with self._sessions() as session:
            payload = (await session.execute(statement)).scalar_one_or_none()
            return None if payload is None else ApprovalRequest.model_validate(payload)

    async def compare_and_set(
        self, expected_status: ApprovalStatus, updated: ApprovalRequest
    ) -> bool:
        statement = (
            update(ApprovalRow)
            .where(
                ApprovalRow.tenant_id == updated.tenant_id,
                ApprovalRow.approval_id == updated.approval_id,
                ApprovalRow.status == expected_status.value,
            )
            .values(
                status=updated.status.value,
                payload=updated.model_dump(mode="json"),
            )
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def list_expired_pending(
        self, expires_at_or_before: datetime, *, limit: int
    ) -> list[ApprovalRequest]:
        statement = (
            select(ApprovalRow.payload)
            .where(
                ApprovalRow.status == ApprovalStatus.PENDING.value,
                ApprovalRow.expires_at <= expires_at_or_before,
            )
            .order_by(ApprovalRow.expires_at, ApprovalRow.approval_id)
            .limit(limit)
        )
        async with self._sessions() as session:
            payloads = (await session.execute(statement)).scalars().all()
            return [ApprovalRequest.model_validate(payload) for payload in payloads]

    async def list_for_runs(self, tenant_id: str, run_ids: list[str]) -> list[ApprovalRequest]:
        if not run_ids:
            return []
        statement = (
            select(ApprovalRow.payload)
            .where(
                ApprovalRow.tenant_id == tenant_id,
                ApprovalRow.run_id.in_(run_ids),
            )
            .order_by(ApprovalRow.approval_id.desc())
        )
        async with self._sessions() as session:
            payloads = (await session.execute(statement)).scalars().all()
            return sorted(
                (ApprovalRequest.model_validate(payload) for payload in payloads),
                key=lambda approval: (approval.created_at, approval.approval_id),
                reverse=True,
            )


class PostgresArtifactRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, artifact: Artifact) -> None:
        async with self._sessions() as session:
            session.add(
                ArtifactRow(
                    tenant_id=artifact.tenant_id,
                    artifact_id=artifact.artifact_id,
                    run_id=artifact.run_id,
                    payload=artifact.model_dump(mode="json"),
                )
            )
            await _commit_add(session, message=f"artifact already exists: {artifact.artifact_id}")

    async def get(self, tenant_id: str, artifact_id: str) -> Artifact:
        async with self._sessions() as session:
            row = await session.get(ArtifactRow, (tenant_id, artifact_id))
            if row is None:
                raise NotFoundError(f"artifact not found: {artifact_id}")
            return Artifact.model_validate(row.payload)

    async def update(self, artifact: Artifact) -> None:
        statement = (
            update(ArtifactRow)
            .where(
                ArtifactRow.tenant_id == artifact.tenant_id,
                ArtifactRow.artifact_id == artifact.artifact_id,
            )
            .values(run_id=artifact.run_id, payload=artifact.model_dump(mode="json"))
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            if not cast(CursorResult[Any], result).rowcount:
                raise NotFoundError(f"artifact not found: {artifact.artifact_id}")

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[Artifact]:
        statement = select(ArtifactRow.payload).where(
            ArtifactRow.tenant_id == tenant_id, ArtifactRow.run_id == run_id
        )
        async with self._sessions() as session:
            return [
                Artifact.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]


class PostgresInputArtifactRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, artifact: InputArtifact) -> None:
        async with self._sessions() as session:
            session.add(
                InputArtifactRow(
                    tenant_id=artifact.tenant_id,
                    input_artifact_id=artifact.input_artifact_id,
                    user_id=artifact.user_id,
                    payload=artifact.model_dump(mode="json"),
                )
            )
            await _commit_add(
                session,
                message=f"input artifact already exists: {artifact.input_artifact_id}",
            )

    async def get(self, tenant_id: str, input_artifact_id: str) -> InputArtifact:
        async with self._sessions() as session:
            row = await session.get(InputArtifactRow, (tenant_id, input_artifact_id))
            if row is None:
                raise NotFoundError(f"input artifact not found: {input_artifact_id}")
            return InputArtifact.model_validate(row.payload)

    async def update(self, artifact: InputArtifact) -> None:
        statement = (
            update(InputArtifactRow)
            .where(
                InputArtifactRow.tenant_id == artifact.tenant_id,
                InputArtifactRow.input_artifact_id == artifact.input_artifact_id,
            )
            .values(user_id=artifact.user_id, payload=artifact.model_dump(mode="json"))
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            if not cast(CursorResult[Any], result).rowcount:
                raise NotFoundError(f"input artifact not found: {artifact.input_artifact_id}")


class PostgresUserMemoryRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, memory: UserMemory) -> None:
        async with self._sessions() as session:
            session.add(
                UserMemoryRow(
                    tenant_id=memory.tenant_id,
                    user_id=memory.user_id,
                    agent_name=memory.agent_name,
                    version=memory.version,
                    payload=memory.model_dump(mode="json"),
                )
            )
            await _commit_add(session, message="user memory already exists")

    async def get(self, tenant_id: str, user_id: str, agent_name: str) -> UserMemory | None:
        async with self._sessions() as session:
            row = await session.get(UserMemoryRow, (tenant_id, user_id, agent_name))
            return None if row is None else UserMemory.model_validate(row.payload)

    async def compare_and_set(self, expected_version: int, updated: UserMemory) -> bool:
        if updated.version != expected_version + 1:
            raise ConflictError("user memory version must increment by one")
        statement = (
            update(UserMemoryRow)
            .where(
                UserMemoryRow.tenant_id == updated.tenant_id,
                UserMemoryRow.user_id == updated.user_id,
                UserMemoryRow.agent_name == updated.agent_name,
                UserMemoryRow.version == expected_version,
            )
            .values(
                version=updated.version,
                payload=updated.model_dump(mode="json"),
            )
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def delete(self, tenant_id: str, user_id: str, agent_name: str) -> None:
        async with self._sessions() as session:
            await session.execute(
                delete(UserMemoryRow).where(
                    UserMemoryRow.tenant_id == tenant_id,
                    UserMemoryRow.user_id == user_id,
                    UserMemoryRow.agent_name == agent_name,
                )
            )
            await session.commit()


class PostgresThreadFileRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, file: ThreadFile) -> None:
        async with self._sessions() as session:
            if file.parent_file_id is not None:
                parent = await session.get(ThreadFileRow, (file.tenant_id, file.parent_file_id))
                if parent is None:
                    raise NotFoundError(f"parent thread file not found: {file.parent_file_id}")
                parent_value = ThreadFile.model_validate(parent.payload)
                if (parent_value.user_id, parent_value.session_id) != (
                    file.user_id,
                    file.session_id,
                ):
                    raise ConflictError("derived file must share its parent's thread scope")
            session.add(
                ThreadFileRow(
                    tenant_id=file.tenant_id,
                    file_id=file.file_id,
                    user_id=file.user_id,
                    session_id=file.session_id,
                    parent_file_id=file.parent_file_id,
                    created_at=file.created_at,
                    payload=file.model_dump(mode="json"),
                )
            )
            await _commit_add(session, message=f"thread file already exists: {file.file_id}")

    async def get(self, tenant_id: str, file_id: str) -> ThreadFile:
        async with self._sessions() as session:
            row = await session.get(ThreadFileRow, (tenant_id, file_id))
            if row is None:
                raise NotFoundError(f"thread file not found: {file_id}")
            return ThreadFile.model_validate(row.payload)

    async def list_for_session(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> list[ThreadFile]:
        statement = (
            select(ThreadFileRow.payload)
            .where(
                ThreadFileRow.tenant_id == tenant_id,
                ThreadFileRow.user_id == user_id,
                ThreadFileRow.session_id == session_id,
            )
            .order_by(ThreadFileRow.created_at, ThreadFileRow.file_id)
        )
        async with self._sessions() as session:
            return [
                ThreadFile.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def list_children(self, tenant_id: str, parent_file_id: str) -> list[ThreadFile]:
        statement = (
            select(ThreadFileRow.payload)
            .where(
                ThreadFileRow.tenant_id == tenant_id,
                ThreadFileRow.parent_file_id == parent_file_id,
            )
            .order_by(ThreadFileRow.created_at, ThreadFileRow.file_id)
        )
        async with self._sessions() as session:
            return [
                ThreadFile.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]


class PostgresWorkspaceSnapshotRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, snapshot: WorkspaceSnapshot) -> None:
        async with self._sessions() as session:
            session.add(
                WorkspaceSnapshotRow(
                    tenant_id=snapshot.tenant_id,
                    snapshot_id=snapshot.snapshot_id,
                    session_id=snapshot.session_id,
                    created_at=snapshot.created_at,
                    payload=snapshot.model_dump(mode="json"),
                )
            )
            await _commit_add(
                session,
                message=f"workspace snapshot already exists: {snapshot.snapshot_id}",
            )

    async def get(self, tenant_id: str, snapshot_id: str) -> WorkspaceSnapshot:
        async with self._sessions() as session:
            row = await session.get(WorkspaceSnapshotRow, (tenant_id, snapshot_id))
            if row is None:
                raise NotFoundError(f"workspace snapshot not found: {snapshot_id}")
            return WorkspaceSnapshot.model_validate(row.payload)

    async def latest(self, tenant_id: str, session_id: str) -> WorkspaceSnapshot | None:
        statement = (
            select(WorkspaceSnapshotRow.payload)
            .where(
                WorkspaceSnapshotRow.tenant_id == tenant_id,
                WorkspaceSnapshotRow.session_id == session_id,
            )
            .order_by(
                WorkspaceSnapshotRow.created_at.desc(),
                WorkspaceSnapshotRow.snapshot_id.desc(),
            )
            .limit(1)
        )
        async with self._sessions() as session:
            payload = (await session.execute(statement)).scalar_one_or_none()
            return None if payload is None else WorkspaceSnapshot.model_validate(payload)


class PostgresAguiThreadBindingRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, binding: AguiThreadBinding) -> None:
        async with self._sessions() as session:
            session.add(
                AguiThreadBindingRow(
                    tenant_id=binding.tenant_id,
                    user_id=binding.user_id,
                    thread_id=binding.thread_id,
                    session_id=binding.session_id,
                    payload=binding.model_dump(mode="json"),
                )
            )
            await _commit_add(session, message="AG-UI thread binding already exists")

    async def get_by_thread(
        self, tenant_id: str, user_id: str, thread_id: str
    ) -> AguiThreadBinding:
        async with self._sessions() as session:
            row = await session.get(AguiThreadBindingRow, (tenant_id, user_id, thread_id))
            if row is None:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}")
            return AguiThreadBinding.model_validate(row.payload)

    async def get_by_session(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> AguiThreadBinding:
        current_statement = select(AguiThreadBindingRow.payload).where(
            AguiThreadBindingRow.tenant_id == tenant_id,
            AguiThreadBindingRow.user_id == user_id,
            AguiThreadBindingRow.session_id == session_id,
        )
        async with self._sessions() as session:
            current = (await session.execute(current_statement)).scalar_one_or_none()
            if current is not None:
                return AguiThreadBinding.model_validate(current)
            history_statement = select(AguiThreadBindingRow.payload).where(
                AguiThreadBindingRow.tenant_id == tenant_id,
                AguiThreadBindingRow.user_id == user_id,
            )
            payloads = (await session.execute(history_statement)).scalars().all()
            for payload in payloads:
                binding = AguiThreadBinding.model_validate(payload)
                if session_id in binding.previous_session_ids:
                    return binding
            raise NotFoundError(f"AG-UI session binding not found: {session_id}")

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int, archived: bool = False
    ) -> list[AguiThreadBinding]:
        archived_at = AguiThreadBindingRow.payload["archived_at"].as_string()
        updated_at = AguiThreadBindingRow.payload["updated_at"].as_string()
        statement = (
            select(AguiThreadBindingRow.payload)
            .where(
                AguiThreadBindingRow.tenant_id == tenant_id,
                AguiThreadBindingRow.user_id == user_id,
                archived_at.is_not(None) if archived else archived_at.is_(None),
            )
            .order_by(updated_at.desc(), AguiThreadBindingRow.thread_id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            payloads = (await session.execute(statement)).scalars().all()
            return [AguiThreadBinding.model_validate(payload) for payload in payloads]

    async def update_title(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        title: str,
        source: Literal["fallback", "model"],
        generated_at: datetime,
    ) -> AguiThreadBinding:
        async with self._sessions() as session:
            row = await session.get(AguiThreadBindingRow, (tenant_id, user_id, thread_id))
            if row is None:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}")
            binding = AguiThreadBinding.model_validate(row.payload)
            if binding.title_updated_at is not None and binding.title_updated_at > generated_at:
                return binding
            updated = binding.model_copy(
                update={
                    "title": title,
                    "title_source": source,
                    "title_updated_at": generated_at,
                    "updated_at": max(binding.updated_at, generated_at),
                }
            )
            row.payload = updated.model_dump(mode="json")
            await session.commit()
            return updated

    async def set_archived(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        archived_at: datetime | None,
    ) -> AguiThreadBinding:
        async with self._sessions() as session:
            row = await session.get(AguiThreadBindingRow, (tenant_id, user_id, thread_id))
            if row is None:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}")
            binding = AguiThreadBinding.model_validate(row.payload)
            updated = binding.model_copy(
                update={
                    "archived_at": archived_at,
                    "updated_at": max(binding.updated_at, archived_at)
                    if archived_at is not None
                    else binding.updated_at,
                }
            )
            row.payload = updated.model_dump(mode="json")
            await session.commit()
            return updated

    async def rebind_session(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        expected_session_id: str,
        session_id: str,
        updated_at: datetime,
    ) -> AguiThreadBinding:
        async with self._sessions() as session:
            row = await session.get(
                AguiThreadBindingRow,
                (tenant_id, user_id, thread_id),
                with_for_update=True,
            )
            if row is None:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}")
            binding = AguiThreadBinding.model_validate(row.payload)
            if binding.session_id != expected_session_id:
                raise ConflictError("AG-UI thread Session changed concurrently")
            if binding.session_id == session_id:
                return binding
            previous = tuple(
                value
                for value in dict.fromkeys((*binding.previous_session_ids, binding.session_id))
                if value != session_id
            )
            updated = binding.model_copy(
                update={
                    "session_id": session_id,
                    "previous_session_ids": previous,
                    "updated_at": max(binding.updated_at, updated_at),
                }
            )
            row.session_id = session_id
            row.payload = updated.model_dump(mode="json")
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError("AG-UI session binding already exists") from error
            return updated
