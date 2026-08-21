"""Deterministic in-memory adapters for tests and no-Docker smoke runs."""

import asyncio
import hashlib
from collections import defaultdict, deque
from datetime import datetime
from typing import Literal

from harness.core.errors import ConflictError, EventSequenceConflictError, NotFoundError
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
from harness.core.ports import RunTask, StoredObject


class InMemoryAgentRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str, str], AgentVersion] = {}
        self._lock = asyncio.Lock()

    async def add(self, version: AgentVersion) -> None:
        key = (
            version.tenant_id,
            version.owner_user_id,
            version.name,
            version.version,
        )
        async with self._lock:
            if key in self._items:
                raise ConflictError(
                    f"agent version already exists: {version.name}@{version.version}"
                )
            self._items[key] = version

    async def get(
        self, tenant_id: str, owner_user_id: str, name: str, version: str
    ) -> AgentVersion:
        try:
            return self._items[(tenant_id, owner_user_id, name, version)]
        except KeyError as error:
            raise NotFoundError(f"agent version not found: {name}@{version}") from error

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        return sorted(
            (
                version
                for (
                    stored_tenant,
                    stored_owner,
                    _name,
                    _version,
                ), version in self._items.items()
                if stored_tenant == tenant_id and stored_owner == owner_user_id
            ),
            key=lambda version: (version.name, version.version),
        )

    async def list_catalog_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        # In-memory values do not incur payload transfer, but preserve the
        # production contract that catalog reads exclude packaged files.
        return [
            version.model_copy(
                update={
                    "snapshot": {
                        "manifest": version.snapshot.get("manifest", {}),
                    }
                }
            )
            for version in await self.list_for_user(tenant_id, owner_user_id)
        ]

    async def move_owner(
        self, tenant_id: str, from_user_id: str, to_user_id: str, name: str
    ) -> int:
        if from_user_id == to_user_id:
            return 0
        moved_keys = [
            key
            for key in self._items
            if key[0] == tenant_id and key[1] == from_user_id and key[2] == name
        ]
        async with self._lock:
            conflicts = [
                (key[2], key[3])
                for key in moved_keys
                if (tenant_id, to_user_id, key[2], key[3]) in self._items
            ]
            if conflicts:
                joined = ", ".join(f"{item[0]}@{item[1]}" for item in sorted(conflicts))
                raise ConflictError(f"target user already owns an Agent version: {joined}")
            for key in moved_keys:
                version = self._items.pop(key)
                self._items[(tenant_id, to_user_id, name, key[3])] = version.model_copy(
                    update={"owner_user_id": to_user_id}
                )
        return len(moved_keys)


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Session] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: Session) -> None:
        key = (session.tenant_id, session.session_id)
        async with self._lock:
            if key in self._items:
                raise ConflictError(f"session already exists: {session.session_id}")
            self._items[key] = session

    async def get(self, tenant_id: str, session_id: str) -> Session:
        try:
            return self._items[(tenant_id, session_id)]
        except KeyError as error:
            raise NotFoundError(f"session not found: {session_id}") from error

    async def list_for_ids(self, tenant_id: str, session_ids: list[str]) -> list[Session]:
        return [await self.get(tenant_id, session_id) for session_id in session_ids]

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
        key = (tenant_id, session_id)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError(f"session not found: {session_id}")
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
            self._items[key] = updated
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
        key = (tenant_id, session_id)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError(f"session not found: {session_id}")
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
            self._items[key] = updated
            return updated


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Run] = {}
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def add(self, run: Run) -> None:
        key = (run.tenant_id, run.run_id)
        idem_key = (run.tenant_id, run.session_id, run.idempotency_key)
        async with self._lock:
            if key in self._items or idem_key in self._idempotency:
                raise ConflictError(f"run already exists: {run.run_id}")
            self._items[key] = run
            self._idempotency[idem_key] = run.run_id

    async def get(self, tenant_id: str, run_id: str) -> Run:
        try:
            return self._items[(tenant_id, run_id)]
        except KeyError as error:
            raise NotFoundError(f"run not found: {run_id}") from error

    async def find_by_idempotency_key(
        self, tenant_id: str, session_id: str, idempotency_key: str
    ) -> Run | None:
        run_id = self._idempotency.get((tenant_id, session_id, idempotency_key))
        return None if run_id is None else self._items[(tenant_id, run_id)]

    async def compare_and_set(self, expected_status: RunStatus, updated: Run) -> bool:
        key = (updated.tenant_id, updated.run_id)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError(f"run not found: {updated.run_id}")
            if (
                current.status is not expected_status
                or current.fencing_token != updated.fencing_token - 1
            ):
                return False
            self._items[key] = updated
            return True

    async def list_for_sessions(
        self, tenant_id: str, session_ids: list[str], *, limit: int
    ) -> list[Run]:
        wanted = set(session_ids)
        matches = [
            run
            for (item_tenant, _), run in self._items.items()
            if item_tenant == tenant_id and run.session_id in wanted
        ]
        return sorted(matches, key=lambda run: (run.updated_at, run.run_id), reverse=True)[:limit]

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[Run]:
        return sorted(
            (run for (stored_tenant, _), run in self._items.items() if stored_tenant == tenant_id),
            key=lambda run: (run.updated_at, run.run_id),
            reverse=True,
        )[:limit]

    async def list_stale(
        self,
        statuses: frozenset[RunStatus],
        updated_at_or_before: datetime,
        *,
        limit: int,
    ) -> list[Run]:
        values = [
            run
            for run in self._items.values()
            if run.status in statuses and run.updated_at <= updated_at_or_before
        ]
        return sorted(values, key=lambda item: (item.updated_at, item.run_id))[:limit]


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ApprovalRequest] = {}
        self._by_tool: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def add(self, approval: ApprovalRequest) -> None:
        key = (approval.tenant_id, approval.approval_id)
        tool_key = (approval.tenant_id, approval.run_id, approval.tool_call_id)
        async with self._lock:
            if key in self._items or tool_key in self._by_tool:
                raise ConflictError(f"approval already exists: {approval.approval_id}")
            self._items[key] = approval
            self._by_tool[tool_key] = approval.approval_id

    async def get(self, tenant_id: str, approval_id: str) -> ApprovalRequest:
        try:
            return self._items[(tenant_id, approval_id)]
        except KeyError as error:
            raise NotFoundError(f"approval not found: {approval_id}") from error

    async def find_by_tool_call(
        self, tenant_id: str, run_id: str, tool_call_id: str
    ) -> ApprovalRequest | None:
        approval_id = self._by_tool.get((tenant_id, run_id, tool_call_id))
        return None if approval_id is None else self._items[(tenant_id, approval_id)]

    async def compare_and_set(
        self, expected_status: ApprovalStatus, updated: ApprovalRequest
    ) -> bool:
        key = (updated.tenant_id, updated.approval_id)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError(f"approval not found: {updated.approval_id}")
            if current.status is not expected_status:
                return False
            self._items[key] = updated
            return True

    async def list_expired_pending(
        self, expires_at_or_before: datetime, *, limit: int
    ) -> list[ApprovalRequest]:
        items = [
            approval
            for approval in self._items.values()
            if approval.status is ApprovalStatus.PENDING
            and approval.expires_at <= expires_at_or_before
        ]
        return sorted(items, key=lambda approval: approval.expires_at)[:limit]

    async def list_for_runs(self, tenant_id: str, run_ids: list[str]) -> list[ApprovalRequest]:
        wanted = set(run_ids)
        return sorted(
            (
                approval
                for (item_tenant, _), approval in self._items.items()
                if item_tenant == tenant_id and approval.run_id in wanted
            ),
            key=lambda approval: (approval.created_at, approval.approval_id),
            reverse=True,
        )


class InMemoryEventRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], list[RunEvent]] = defaultdict(list)
        self._by_id: dict[str, RunEvent] = {}
        self._lock = asyncio.Lock()

    async def append(self, event: RunEvent) -> None:
        async with self._lock:
            existing = self._by_id.get(event.event_id)
            if existing is not None:
                if existing != event:
                    raise ConflictError(
                        f"event id already contains different data: {event.event_id}"
                    )
                return
            key = (event.tenant_id, event.run_id)
            expected_sequence = len(self._items[key]) + 1
            if event.sequence != expected_sequence:
                raise EventSequenceConflictError(
                    f"event sequence must be {expected_sequence}, got {event.sequence}"
                )
            self._items[key].append(event)
            self._by_id[event.event_id] = event

    async def latest_sequence(self, tenant_id: str, run_id: str) -> int:
        events = self._items[(tenant_id, run_id)]
        return events[-1].sequence if events else 0

    async def list_after(self, tenant_id: str, run_id: str, after_sequence: int) -> list[RunEvent]:
        return [
            event for event in self._items[(tenant_id, run_id)] if event.sequence > after_sequence
        ]

    async def latest_for_session_type(
        self, tenant_id: str, session_id: str, event_type: str
    ) -> RunEvent | None:
        return await self.latest_for_session_types(tenant_id, session_id, (event_type,))

    async def latest_for_session_types(
        self, tenant_id: str, session_id: str, event_types: tuple[str, ...]
    ) -> RunEvent | None:
        wanted = set(event_types)
        matches = (
            event
            for (stored_tenant, _), events in self._items.items()
            if stored_tenant == tenant_id
            for event in events
            if event.session_id == session_id and event.type in wanted
        )
        return max(
            matches,
            key=lambda event: (event.timestamp, event.run_id, event.sequence),
            default=None,
        )


class InMemoryEventBus:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], list[RunEvent]] = defaultdict(list)
        self._conditions: dict[tuple[str, str], asyncio.Condition] = defaultdict(asyncio.Condition)

    async def publish(self, event: RunEvent) -> None:
        key = (event.tenant_id, event.run_id)
        async with self._conditions[key]:
            events = self._items[key]
            if all(existing.event_id != event.event_id for existing in events):
                events.append(event)
                self._conditions[key].notify_all()

    async def read(self, tenant_id: str, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
        return [
            event for event in self._items[(tenant_id, run_id)] if event.sequence > after_sequence
        ]

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float,
    ) -> bool:
        key = (tenant_id, run_id)
        condition = self._conditions[key]
        async with condition:
            if any(event.sequence > after_sequence for event in self._items[key]):
                return True
            try:
                await asyncio.wait_for(condition.wait(), timeout=timeout_seconds)
            except TimeoutError:
                return False
            return any(event.sequence > after_sequence for event in self._items[key])


class InMemoryCancellationWakeup:
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], int] = defaultdict(int)
        self._conditions: dict[tuple[str, str], asyncio.Condition] = defaultdict(asyncio.Condition)

    async def publish(self, tenant_id: str, run_id: str, fencing_token: int) -> None:
        key = (tenant_id, run_id)
        async with self._conditions[key]:
            self._tokens[key] = max(self._tokens[key], fencing_token)
            self._conditions[key].notify_all()

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_fencing_token: int,
        *,
        timeout_seconds: float,
    ) -> bool:
        key = (tenant_id, run_id)
        condition = self._conditions[key]
        async with condition:
            if self._tokens[key] > after_fencing_token:
                return True
            try:
                await asyncio.wait_for(condition.wait(), timeout=timeout_seconds)
            except TimeoutError:
                return False
            return self._tokens[key] > after_fencing_token


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], bytes] = {}

    async def put(self, tenant_id: str, artifact_id: str, content: bytes) -> StoredObject:
        self._items[(tenant_id, artifact_id)] = content
        return StoredObject(
            object_key=f"{tenant_id}/{artifact_id}",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    async def get(self, tenant_id: str, artifact_id: str) -> bytes:
        try:
            return self._items[(tenant_id, artifact_id)]
        except KeyError as error:
            raise NotFoundError(f"artifact not found: {artifact_id}") from error

    async def delete(self, tenant_id: str, artifact_id: str) -> None:
        self._items.pop((tenant_id, artifact_id), None)


class InMemoryArtifactRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Artifact] = {}
        self._lock = asyncio.Lock()

    async def add(self, artifact: Artifact) -> None:
        key = (artifact.tenant_id, artifact.artifact_id)
        async with self._lock:
            if key in self._items:
                raise ConflictError(f"artifact already exists: {artifact.artifact_id}")
            self._items[key] = artifact

    async def get(self, tenant_id: str, artifact_id: str) -> Artifact:
        try:
            return self._items[(tenant_id, artifact_id)]
        except KeyError as error:
            raise NotFoundError(f"artifact not found: {artifact_id}") from error

    async def update(self, artifact: Artifact) -> None:
        key = (artifact.tenant_id, artifact.artifact_id)
        async with self._lock:
            if key not in self._items:
                raise NotFoundError(f"artifact not found: {artifact.artifact_id}")
            self._items[key] = artifact

    async def list_for_run(self, tenant_id: str, run_id: str) -> list[Artifact]:
        return [
            artifact
            for (item_tenant, _), artifact in self._items.items()
            if item_tenant == tenant_id and artifact.run_id == run_id
        ]


class InMemoryInputArtifactRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], InputArtifact] = {}
        self._lock = asyncio.Lock()

    async def add(self, artifact: InputArtifact) -> None:
        key = (artifact.tenant_id, artifact.input_artifact_id)
        async with self._lock:
            if key in self._items:
                raise ConflictError(f"input artifact already exists: {artifact.input_artifact_id}")
            self._items[key] = artifact

    async def get(self, tenant_id: str, input_artifact_id: str) -> InputArtifact:
        try:
            return self._items[(tenant_id, input_artifact_id)]
        except KeyError as error:
            raise NotFoundError(f"input artifact not found: {input_artifact_id}") from error

    async def update(self, artifact: InputArtifact) -> None:
        key = (artifact.tenant_id, artifact.input_artifact_id)
        async with self._lock:
            if key not in self._items:
                raise NotFoundError(f"input artifact not found: {artifact.input_artifact_id}")
            self._items[key] = artifact


class InMemoryUserMemoryRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], UserMemory] = {}
        self._lock = asyncio.Lock()

    async def add(self, memory: UserMemory) -> None:
        key = (memory.tenant_id, memory.user_id, memory.agent_name)
        async with self._lock:
            if key in self._items:
                raise ConflictError("user memory already exists")
            self._items[key] = memory

    async def get(self, tenant_id: str, user_id: str, agent_name: str) -> UserMemory | None:
        return self._items.get((tenant_id, user_id, agent_name))

    async def compare_and_set(self, expected_version: int, updated: UserMemory) -> bool:
        key = (updated.tenant_id, updated.user_id, updated.agent_name)
        async with self._lock:
            current = self._items.get(key)
            if current is None:
                raise NotFoundError("user memory not found")
            if current.version != expected_version:
                return False
            if updated.version != expected_version + 1:
                raise ConflictError("user memory version must increment by one")
            self._items[key] = updated
            return True

    async def delete(self, tenant_id: str, user_id: str, agent_name: str) -> None:
        self._items.pop((tenant_id, user_id, agent_name), None)


class InMemoryThreadFileRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ThreadFile] = {}
        self._lock = asyncio.Lock()

    async def add(self, file: ThreadFile) -> None:
        key = (file.tenant_id, file.file_id)
        async with self._lock:
            if key in self._items:
                raise ConflictError(f"thread file already exists: {file.file_id}")
            if file.parent_file_id is not None:
                parent = self._items.get((file.tenant_id, file.parent_file_id))
                if parent is None:
                    raise NotFoundError(f"parent thread file not found: {file.parent_file_id}")
                if (parent.user_id, parent.session_id) != (file.user_id, file.session_id):
                    raise ConflictError("derived file must share its parent's thread scope")
            self._items[key] = file

    async def get(self, tenant_id: str, file_id: str) -> ThreadFile:
        try:
            return self._items[(tenant_id, file_id)]
        except KeyError as error:
            raise NotFoundError(f"thread file not found: {file_id}") from error

    async def list_for_session(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> list[ThreadFile]:
        return [
            file
            for (item_tenant, _), file in self._items.items()
            if item_tenant == tenant_id
            and file.user_id == user_id
            and file.session_id == session_id
        ]

    async def list_children(self, tenant_id: str, parent_file_id: str) -> list[ThreadFile]:
        return [
            file
            for (item_tenant, _), file in self._items.items()
            if item_tenant == tenant_id and file.parent_file_id == parent_file_id
        ]


class InMemoryWorkspaceSnapshotRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], WorkspaceSnapshot] = {}
        self._lock = asyncio.Lock()

    async def add(self, snapshot: WorkspaceSnapshot) -> None:
        key = (snapshot.tenant_id, snapshot.snapshot_id)
        async with self._lock:
            if key in self._items:
                raise ConflictError(f"workspace snapshot already exists: {snapshot.snapshot_id}")
            self._items[key] = snapshot

    async def get(self, tenant_id: str, snapshot_id: str) -> WorkspaceSnapshot:
        try:
            return self._items[(tenant_id, snapshot_id)]
        except KeyError as error:
            raise NotFoundError(f"workspace snapshot not found: {snapshot_id}") from error

    async def latest(self, tenant_id: str, session_id: str) -> WorkspaceSnapshot | None:
        matches = [
            snapshot
            for (item_tenant, _), snapshot in self._items.items()
            if item_tenant == tenant_id and snapshot.session_id == session_id
        ]
        return max(matches, key=lambda item: item.created_at, default=None)


class InMemoryAguiThreadBindingRepository:
    def __init__(self) -> None:
        self._by_thread: dict[tuple[str, str, str], AguiThreadBinding] = {}
        self._by_session: dict[tuple[str, str, str], AguiThreadBinding] = {}
        self._lock = asyncio.Lock()

    async def add(self, binding: AguiThreadBinding) -> None:
        thread_key = (binding.tenant_id, binding.user_id, binding.thread_id)
        session_key = (binding.tenant_id, binding.user_id, binding.session_id)
        async with self._lock:
            if thread_key in self._by_thread or session_key in self._by_session:
                raise ConflictError("AG-UI thread binding already exists")
            self._by_thread[thread_key] = binding
            self._by_session[session_key] = binding

    async def get_by_thread(
        self, tenant_id: str, user_id: str, thread_id: str
    ) -> AguiThreadBinding:
        try:
            return self._by_thread[(tenant_id, user_id, thread_id)]
        except KeyError as error:
            raise NotFoundError(f"AG-UI thread binding not found: {thread_id}") from error

    async def get_by_session(
        self, tenant_id: str, user_id: str, session_id: str
    ) -> AguiThreadBinding:
        try:
            return self._by_session[(tenant_id, user_id, session_id)]
        except KeyError as error:
            raise NotFoundError(f"AG-UI session binding not found: {session_id}") from error

    def _store_session_aliases(self, binding: AguiThreadBinding) -> None:
        for session_id in binding.session_ids:
            self._by_session[(binding.tenant_id, binding.user_id, session_id)] = binding

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, limit: int, archived: bool = False
    ) -> list[AguiThreadBinding]:
        matches = [
            binding
            for (item_tenant, item_user, _), binding in self._by_thread.items()
            if item_tenant == tenant_id
            and item_user == user_id
            and (binding.archived_at is not None) is archived
        ]
        return sorted(
            matches,
            key=lambda binding: (binding.updated_at, binding.thread_id),
            reverse=True,
        )[:limit]

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
        thread_key = (tenant_id, user_id, thread_id)
        async with self._lock:
            try:
                binding = self._by_thread[thread_key]
            except KeyError as error:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}") from error
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
            self._by_thread[thread_key] = updated
            self._store_session_aliases(updated)
            return updated

    async def set_archived(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        archived_at: datetime | None,
    ) -> AguiThreadBinding:
        thread_key = (tenant_id, user_id, thread_id)
        async with self._lock:
            try:
                binding = self._by_thread[thread_key]
            except KeyError as error:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}") from error
            updated = binding.model_copy(
                update={
                    "archived_at": archived_at,
                    "updated_at": max(binding.updated_at, archived_at)
                    if archived_at is not None
                    else binding.updated_at,
                }
            )
            self._by_thread[thread_key] = updated
            self._store_session_aliases(updated)
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
        thread_key = (tenant_id, user_id, thread_id)
        async with self._lock:
            try:
                binding = self._by_thread[thread_key]
            except KeyError as error:
                raise NotFoundError(f"AG-UI thread binding not found: {thread_id}") from error
            if binding.session_id != expected_session_id:
                raise ConflictError("AG-UI thread Session changed concurrently")
            if binding.session_id == session_id:
                return binding
            session_key = (tenant_id, user_id, session_id)
            existing = self._by_session.get(session_key)
            if existing is not None and existing.thread_id != thread_id:
                raise ConflictError("AG-UI session binding already exists")
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
            self._by_thread[thread_key] = updated
            self._store_session_aliases(updated)
            return updated


class InMemoryTaskQueue:
    def __init__(self) -> None:
        self._items: deque[RunTask] = deque()
        self._pending: set[tuple[str, str]] = set()
        self._leased: set[tuple[str, str]] = set()

    async def enqueue(self, task: RunTask) -> None:
        key = (task.tenant_id, task.run_id)
        if key not in self._pending:
            self._pending.add(key)
            self._items.append(task)

    async def dequeue(self) -> RunTask | None:
        if not self._items:
            return None
        task = self._items.popleft()
        self._leased.add((task.tenant_id, task.run_id))
        return task

    async def acknowledge(self, task: RunTask) -> None:
        key = (task.tenant_id, task.run_id)
        self._leased.discard(key)
        self._pending.discard(key)

    async def retry(self, task: RunTask) -> None:
        key = (task.tenant_id, task.run_id)
        if key in self._leased:
            self._leased.remove(key)
            self._items.append(task)

    async def extend_lease(self, task: RunTask) -> None:
        del task

    async def stats(self) -> dict[str, int]:
        return {"ready": len(self._items), "processing": len(self._leased)}
