"""Immutable domain models shared by all Harness adapters."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgentRuntimeType = Literal["claude-agent-sdk", "codex-app-server"]


class FrozenModel(BaseModel):
    """Base model for facts that are replaced instead of mutated."""

    model_config = ConfigDict(frozen=True)


class AgentVersionStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class RunStatus(StrEnum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"

    @classmethod
    def terminal_statuses(cls) -> tuple["RunStatus", ...]:
        return (cls.CANCELLED, cls.SUCCEEDED, cls.FAILED, cls.TIMED_OUT, cls.REJECTED)

    @property
    def is_terminal(self) -> bool:
        return self in self.terminal_statuses()


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class ThreadFileKind(StrEnum):
    ORIGINAL = "original"
    DERIVED = "derived"
    GENERATED = "generated"


class ProcessingStatus(StrEnum):
    PROCESSED = "processed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class ModelCompatibility(StrEnum):
    FULL = "full"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class ExecutionIdentity(FrozenModel):
    """Non-secret scope used to resolve all request-specific capabilities."""

    tenant_id: str
    user_id: str
    agent_owner_user_id: str | None = None
    team_ids: tuple[str, ...] = ()
    project_id: str
    session_id: str
    run_id: str
    agent_name: str
    agent_version: str
    # caller_owned resolves MCP credentials by the running user;
    # service_owned resolves them by the pinned team space.
    connection_mode: Literal["caller_owned", "service_owned"] = "caller_owned"

    @property
    def resolved_agent_owner_user_id(self) -> str:
        return self.agent_owner_user_id or self.user_id


class AgentVersion(FrozenModel):
    tenant_id: str
    owner_user_id: str
    name: str
    version: str
    status: AgentVersionStatus
    manifest_hash: str
    package_hash: str | None = None
    snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Stable Agent identity assigned by the workspace model. None only for
    # versions persisted before the 0023 migration backfill.
    agent_id: str | None = None


class Session(FrozenModel):
    session_id: str
    tenant_id: str
    user_id: str
    agent_owner_user_id: str | None = None
    agent_name: str
    agent_version: str
    created_at: datetime
    team_ids: tuple[str, ...] = ()
    api_key_id: str | None = None
    runtime_type: AgentRuntimeType = "claude-agent-sdk"
    runtime_thread_id: str | None = None
    # Kept while stored sessions and API clients migrate to runtime_thread_id.
    claude_session_id: str | None = None
    workspace_snapshot_id: str | None = None
    environment: str | None = None
    deployment_snapshot_id: str | None = None
    environment_snapshot: dict[str, Any] | None = None
    knowledge_snapshot_bindings: tuple[dict[str, Any], ...] = ()
    # Credential resolution mode of the pinned shared Agent: caller_owned
    # resolves MCP credentials by the running user; service_owned uses
    # workspace-provided shared credentials.
    connection_mode: Literal["caller_owned", "service_owned"] = "caller_owned"

    @property
    def resolved_agent_owner_user_id(self) -> str:
        """Owner pinned for runtime lookup; legacy sessions fall back to task owner."""
        return self.agent_owner_user_id or self.user_id

    @property
    def resolved_runtime_thread_id(self) -> str | None:
        """Return the native thread ID, including legacy Claude-only records."""
        return self.runtime_thread_id or self.claude_session_id


class Run(FrozenModel):
    run_id: str
    session_id: str
    tenant_id: str
    status: RunStatus
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    fencing_token: int = 0
    error_code: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    trace_context: dict[str, str] = Field(default_factory=dict)


class Message(FrozenModel):
    message_id: str
    run_id: str
    session_id: str
    tenant_id: str
    role: MessageRole
    content: list[dict[str, Any]]
    created_at: datetime


class ToolCall(FrozenModel):
    tool_call_id: str
    run_id: str
    name: str
    arguments: dict[str, Any]
    created_at: datetime


class ApprovalRequest(FrozenModel):
    approval_id: str
    run_id: str
    tenant_id: str
    tool_call_id: str
    status: ApprovalStatus
    reason: str
    expires_at: datetime
    created_at: datetime
    inline: bool = False
    tool_name: str | None = None
    argument_summary: dict[str, Any] = Field(default_factory=dict)
    sandbox_provider: str | None = None
    sandbox_isolation: str | None = None
    policy_rule: str | None = None
    risk: str | None = None


class Artifact(FrozenModel):
    artifact_id: str
    run_id: str
    tenant_id: str
    name: str
    media_type: str
    status: ArtifactStatus
    object_key: str
    sha256: str | None = None
    size_bytes: int | None = None


class InputArtifact(FrozenModel):
    input_artifact_id: str
    tenant_id: str
    user_id: str
    name: str
    media_type: str
    status: ArtifactStatus
    object_key: str
    created_at: datetime
    sha256: str | None = None
    size_bytes: int | None = None


class UserMemory(FrozenModel):
    tenant_id: str
    user_id: str
    agent_name: str
    content: str
    version: int = Field(ge=1)
    updated_at: datetime


class ThreadFile(FrozenModel):
    file_id: str
    tenant_id: str
    user_id: str
    session_id: str
    run_id: str
    kind: ThreadFileKind
    name: str
    media_type: str
    path: str
    created_at: datetime
    input_artifact_id: str | None = None
    artifact_id: str | None = None
    parent_file_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessedInput(FrozenModel):
    source_file_id: str
    status: ProcessingStatus
    derived_file_ids: tuple[str, ...] = ()
    processor: str
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AguiThreadBinding(FrozenModel):
    tenant_id: str
    user_id: str
    thread_id: str
    session_id: str
    previous_session_ids: tuple[str, ...] = ()
    title: str | None = None
    title_source: Literal["fallback", "model"] | None = None
    title_updated_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def session_ids(self) -> tuple[str, ...]:
        return (*self.previous_session_ids, self.session_id)


class WorkspaceSnapshot(FrozenModel):
    snapshot_id: str
    session_id: str
    tenant_id: str
    object_key: str
    sha256: str
    created_at: datetime


class ModelRoute(FrozenModel):
    route_id: str
    provider: str
    base_url: str
    model: str
    compatibility: ModelCompatibility
    capabilities: frozenset[str] = frozenset()
    fallback_route_id: str | None = None
    auth_scheme: Literal["bearer", "x-api-key"] | None = None
