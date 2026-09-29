"""Framework-independent ports implemented by infrastructure adapters."""

from datetime import datetime
from enum import StrEnum
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


class ExecutionCommandStatus(StrEnum):
    """Lifecycle of one durable "hand this Run to the queue" intent."""

    PENDING = "pending"
    DISPATCHED = "dispatched"


def execution_command_id(run_id: str) -> str:
    """Derive the one stable command id a logical Run can ever have.

    Deriving it from the Run makes the intent idempotent by construction: a
    repeated acceptance attempt for the same Run targets the same row, so the
    platform can never accumulate two competing dispatch intents for one Run.
    """

    return f"dispatch:{run_id}"


class RunExecutionCommand(BaseModel):
    """Durable record of the obligation to deliver one Run to the Run queue.

    This is the single authority for pending Run dispatch. The Redis queue is
    only a delivery transport: a command in ``PENDING`` is still owed even when
    Redis is unreachable, and the Dispatcher keeps retrying it until the queue
    acknowledges the hand-off.
    """

    model_config = ConfigDict(frozen=True)

    command_id: str
    tenant_id: str
    run_id: str
    session_id: str | None = None
    status: ExecutionCommandStatus = ExecutionCommandStatus.PENDING
    created_at: datetime
    # Next moment the command may be claimed. Backoff pushes it forward.
    available_at: datetime
    # Delivery attempts (incremented on every claim) and delivery failures
    # (incremented on every release-for-retry) are tracked separately so an
    # operator can tell "busy retrying" from "keeps failing to publish".
    attempts: int = 0
    failures: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    dispatched_at: datetime | None = None
    last_error: str | None = None

    def to_task(self) -> RunTask:
        return RunTask(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            session_id=self.session_id,
        )


class RunExecutionCommandBacklog(BaseModel):
    """Bounded observation of the dispatch obligation table."""

    model_config = ConfigDict(frozen=True)

    # Every obligation not yet handed to the queue, whatever its lease state.
    pending: int = 0
    # Claimable right now: pending, past its backoff, and not lease-held.
    ready: int = 0
    # Pending under a live lease, i.e. a Dispatcher is publishing it now.
    leased: int = 0
    dispatched: int = 0
    # Age of the oldest outstanding obligation; the silent-backlog signal.
    oldest_pending_age_seconds: float | None = None


class RunExecutionCommandRepository(Protocol):
    """Lease-based work queue inside PostgreSQL for pending Run dispatch."""

    async def claim_pending(
        self,
        *,
        owner: str,
        lease_seconds: float,
        limit: int,
        now: datetime,
    ) -> list[RunExecutionCommand]:
        """Lease up to ``limit`` deliverable commands for ``owner``.

        Claimable means pending, past its ``available_at`` and either unleased
        or holding an expired lease. Concurrent claimants never receive the
        same command.
        """
        ...

    async def mark_dispatched(self, command: RunExecutionCommand, *, now: datetime) -> bool:
        """Close the lease after a successful publish.

        Scoped to the current lease owner, so a command already reclaimed by
        another Dispatcher is never silently marked done here. Returns whether
        this owner still held the lease.
        """
        ...

    async def reschedule(
        self, command: RunExecutionCommand, *, available_at: datetime, error: str | None
    ) -> bool:
        """Return the command to the pending pool after a failed publish."""
        ...

    async def get(self, tenant_id: str, run_id: str) -> RunExecutionCommand | None: ...

    async def backlog(self, *, now: datetime) -> RunExecutionCommandBacklog: ...


class RunAcceptanceUnitOfWork(Protocol):
    """Atomically persist everything that makes an accepted Run true.

    A Run the platform has accepted must already carry its execution intent.
    Writing the Run, its ``run.queued`` event and the dispatch command through
    one transaction is what makes that guarantee hold when the process dies
    between the acceptance response and any queue publish.
    """

    async def accept(
        self,
        run: Run,
        event: RunEvent,
        command: RunExecutionCommand | None,
    ) -> None:
        """Commit Run, event and (when queued) the dispatch command together.

        Raises ``ConflictError`` when the Run already exists, which callers
        resolve by returning the established Run. ``command`` is ``None`` for
        inline child Runs, which must never become another Worker's work item.
        """
        ...

    async def ensure_command(self, command: RunExecutionCommand) -> bool:
        """Insert the dispatch command only when the Run has none yet.

        This is the recovery path for a retry of an already-accepted request:
        the Run is stable, and a missing intent is restored without creating a
        second command or touching an existing one. Returns whether a row was
        inserted.
        """
        ...


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

    async def route_references(self, tenant_id: str, route_id: str) -> tuple[str, ...]:
        """List published ``name@version`` coordinates pinned to one model route."""
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

    async def archive_personal_agent(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_id: str,
        name: str,
    ) -> None:
        """Hide a personal Agent while retaining immutable releases for history."""
        ...


class SessionRepository(Protocol):
    async def list_studio_previews(
        self, tenant_id: str, user_id: str, draft_id: str, *, limit: int
    ) -> list[Session]: ...

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
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
        *,
        types: tuple[str, ...] | None = None,
    ) -> list[RunEvent]: ...

    async def latest_for_session_type(
        self, tenant_id: str, session_id: str, event_type: str
    ) -> RunEvent | None: ...

    async def latest_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...]
    ) -> RunEvent | None: ...


    async def recent_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...],
        *, limit: int = 20, before: RunEvent | None = None,
        exclude_run_id: str | None = None,
    ) -> list[RunEvent]: ...


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

    async def list_for_runs(self, tenant_id: str, run_ids: list[str]) -> list[Artifact]: ...


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
        source: Literal["fallback", "model", "user"],
        generated_at: datetime,
    ) -> AguiThreadBinding: ...

    async def mark_read(
        self, tenant_id: str, user_id: str, thread_id: str, *, read_at: datetime
    ) -> AguiThreadBinding: ...

    async def set_archived(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        archived_at: datetime | None,
    ) -> AguiThreadBinding: ...

    async def set_pinned(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        pinned_at: datetime | None,
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
