"""Framework-independent ports implemented by infrastructure adapters."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from harness.core.events import RunEvent
from harness.core.models import (
    AgentRuntimeType,
    AgentVersion,
    AguiThreadBinding,
    ApprovalRequest,
    ApprovalStatus,
    Artifact,
    InputArtifact,
    Run,
    RunStatus,
    Session,
    ThreadFile,
    UserMemory,
    WorkspaceSnapshot,
)


class StoredObject(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_key: str
    sha256: str
    size_bytes: int


class RunTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    run_id: str
    # Run queues created before concurrent workers did not include this field.
    # Keeping it optional preserves wire compatibility with already-enqueued tasks.
    session_id: str | None = None


class AgentRegistry(Protocol):
    async def add(self, version: AgentVersion) -> None: ...

    async def get(
        self, tenant_id: str, owner_user_id: str, name: str, version: str
    ) -> AgentVersion: ...

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]: ...

    async def list_catalog_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        """List versions with only the manifest portion of ``snapshot`` loaded.

        Runtime callers that need packaged files must continue to use ``get``.
        """
        ...

    async def move_owner(
        self, tenant_id: str, from_user_id: str, to_user_id: str, name: str
    ) -> int:
        """Re-key every immutable version of one personal Agent to a new owner.

        The stable agent_id is preserved, so version history follows the
        identity. Raises ConflictError when the target already owns a version
        with the same name@version coordinate. Returns the number of moved
        rows.
        """
        ...


class AgentIdentityProvider(Protocol):
    """Assigns stable personal Agent identities to publications."""

    async def get_or_create_personal_agent_id(
        self, tenant_id: str, owner_user_id: str, name: str
    ) -> str: ...

    async def promote_personal_agent_version(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_id: str,
        name: str,
        version: str,
    ) -> None:
        """Move a personal Agent's current pointer after a new publication.

        Workspace-scoped identities are ignored because their release pointer
        is governed by the owning team space.
        """
        ...


class SessionRepository(Protocol):
    async def add(self, session: Session) -> None: ...

    async def get(self, tenant_id: str, session_id: str) -> Session: ...

    async def list_for_ids(self, tenant_id: str, session_ids: list[str]) -> list[Session]: ...

    async def bind_runtime_thread(
        self,
        tenant_id: str,
        session_id: str,
        runtime_type: AgentRuntimeType,
        runtime_thread_id: str,
    ) -> Session: ...

    async def clear_runtime_thread(
        self,
        tenant_id: str,
        session_id: str,
        runtime_type: AgentRuntimeType,
        expected_runtime_thread_id: str,
    ) -> Session: ...

    async def bind_claude_session_id(
        self, tenant_id: str, session_id: str, claude_session_id: str
    ) -> Session: ...

    async def clear_claude_session_id(
        self, tenant_id: str, session_id: str, expected_claude_session_id: str
    ) -> Session: ...


class RunRepository(Protocol):
    async def add(self, run: Run) -> None: ...

    async def get(self, tenant_id: str, run_id: str) -> Run: ...

    async def find_by_idempotency_key(
        self, tenant_id: str, session_id: str, idempotency_key: str
    ) -> Run | None: ...

    async def compare_and_set(self, expected_status: RunStatus, updated: Run) -> bool: ...

    async def list_for_sessions(
        self, tenant_id: str, session_ids: list[str], *, limit: int
    ) -> list[Run]: ...

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[Run]: ...


class ApprovalRepository(Protocol):
    async def add(self, approval: ApprovalRequest) -> None: ...

    async def get(self, tenant_id: str, approval_id: str) -> ApprovalRequest: ...

    async def find_by_tool_call(
        self, tenant_id: str, run_id: str, tool_call_id: str
    ) -> ApprovalRequest | None: ...

    async def compare_and_set(
        self, expected_status: ApprovalStatus, updated: ApprovalRequest
    ) -> bool: ...

    async def list_expired_pending(
        self, expires_at_or_before: datetime, *, limit: int
    ) -> list[ApprovalRequest]: ...

    async def list_for_runs(self, tenant_id: str, run_ids: list[str]) -> list[ApprovalRequest]: ...


class EventRepository(Protocol):
    async def append(self, event: RunEvent) -> None: ...

    async def latest_sequence(self, tenant_id: str, run_id: str) -> int: ...

    async def list_after(
        self, tenant_id: str, run_id: str, after_sequence: int
    ) -> list[RunEvent]: ...

    async def latest_for_session_type(
        self, tenant_id: str, session_id: str, event_type: str
    ) -> RunEvent | None: ...

    async def latest_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...]
    ) -> RunEvent | None: ...


class EventBus(Protocol):
    async def publish(self, event: RunEvent) -> None: ...

    async def read(
        self, tenant_id: str, run_id: str, after_sequence: int = 0
    ) -> list[RunEvent]: ...


class EventWakeup(Protocol):
    """Best-effort notification that durable events may be ready to read."""

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float,
    ) -> bool: ...


class CancellationWakeup(Protocol):
    """Best-effort cancellation signal guarded by a durable fencing token."""

    async def publish(
        self,
        tenant_id: str,
        run_id: str,
        fencing_token: int,
    ) -> None: ...

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_fencing_token: int,
        *,
        timeout_seconds: float,
    ) -> bool: ...


class ArtifactStore(Protocol):
    async def put(self, tenant_id: str, artifact_id: str, content: bytes) -> StoredObject: ...

    async def get(self, tenant_id: str, artifact_id: str) -> bytes: ...

    async def delete(self, tenant_id: str, artifact_id: str) -> None: ...


class ArtifactRepository(Protocol):
    async def add(self, artifact: Artifact) -> None: ...

    async def get(self, tenant_id: str, artifact_id: str) -> Artifact: ...

    async def update(self, artifact: Artifact) -> None: ...

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[Artifact]: ...


class InputArtifactRepository(Protocol):
    async def add(self, artifact: InputArtifact) -> None: ...

    async def get(self, tenant_id: str, input_artifact_id: str) -> InputArtifact: ...

    async def update(self, artifact: InputArtifact) -> None: ...


class UserMemoryRepository(Protocol):
    async def add(self, memory: UserMemory) -> None: ...

    async def get(self, tenant_id: str, user_id: str, agent_name: str) -> UserMemory | None: ...

    async def compare_and_set(self, expected_version: int, updated: UserMemory) -> bool: ...

    async def delete(self, tenant_id: str, user_id: str, agent_name: str) -> None: ...


class ThreadFileRepository(Protocol):
    async def add(self, file: ThreadFile) -> None: ...

    async def get(self, tenant_id: str, file_id: str) -> ThreadFile: ...

    async def list_for_session(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> list[ThreadFile]: ...

    async def list_children(self, tenant_id: str, parent_file_id: str) -> list[ThreadFile]: ...


class WorkspaceSnapshotRepository(Protocol):
    async def add(self, snapshot: WorkspaceSnapshot) -> None: ...

    async def get(self, tenant_id: str, snapshot_id: str) -> WorkspaceSnapshot: ...

    async def latest(self, tenant_id: str, session_id: str) -> WorkspaceSnapshot | None: ...


class AguiThreadBindingRepository(Protocol):
    async def add(self, binding: AguiThreadBinding) -> None: ...

    async def get_by_thread(
        self, tenant_id: str, user_id: str, thread_id: str
    ) -> AguiThreadBinding: ...

    async def get_by_session(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> AguiThreadBinding: ...

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int, archived: bool = False
    ) -> list[AguiThreadBinding]: ...

    async def update_title(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        title: str,
        source: Literal["fallback", "model"],
        generated_at: datetime,
    ) -> AguiThreadBinding: ...

    async def set_archived(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        archived_at: datetime | None,
    ) -> AguiThreadBinding: ...

    async def rebind_session(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        expected_session_id: str,
        session_id: str,
        updated_at: datetime,
    ) -> AguiThreadBinding: ...


class TaskQueue(Protocol):
    async def enqueue(self, task: RunTask) -> None: ...

    async def dequeue(self) -> RunTask | None: ...

    async def acknowledge(self, task: RunTask) -> None: ...

    async def retry(self, task: RunTask) -> None: ...

    async def extend_lease(self, task: RunTask) -> None: ...
