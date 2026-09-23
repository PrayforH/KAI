"""Runtime contract independent from a specific Agent SDK."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.core.models import ExecutionIdentity, Run, Session
from harness.policy.runtime import ResolvedPolicy
from harness.runtime.artifact_tools import ArtifactPublisher
from harness.runtime.steering import SteeringInbox
from harness.sandbox.base import SandboxCommandResult, SandboxEnforcement, SandboxIsolation

RuntimeTransportFactory = Callable[[object], object]
SandboxCommandExecutor = Callable[
    [Sequence[str], Mapping[str, str] | None, float],
    Awaitable[SandboxCommandResult],
]


@runtime_checkable
class SandboxFilePlane(Protocol):
    """Byte-faithful file access to the Run workspace, when the sandbox has one.

    A command-only channel forces every file operation through the command plane,
    where bytes travel as an encoded argument: that pays a round trip per file,
    loses byte fidelity for the caller's own encoding, and makes the platform
    guess from the command's shape whether the workspace changed. A backend that
    can reach its file API directly exposes this instead, and callers prefer it
    whenever it is present.

    Paths are workspace-relative, the same spelling the tool gate authorizes.
    """

    async def upload_files(self, entries: Sequence[tuple[str, bytes]]) -> None: ...

    async def download_file(self, path: str, *, max_bytes: int) -> bytes: ...


class RuntimeExecutionTimeoutError(TimeoutError):
    """Raised when a Manifest runtime timeout is exhausted."""


class RuntimeResultError(RuntimeError):
    """Raised when an Agent runtime returns a terminal error result."""

    def __init__(
        self,
        subtype: str,
        api_error_status: int | None = None,
        *,
        error_code: str = "runtime_result_error",
        user_message: str | None = None,
    ) -> None:
        self.subtype = subtype
        self.api_error_status = api_error_status
        self.error_code = error_code
        self.user_message = user_message
        super().__init__(f"Agent runtime returned an error result: {subtype}")


class RuntimeContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    run: Run
    session: Session
    workspace: Path
    sandbox_provider: str = "local"
    sandbox_isolation: SandboxIsolation = SandboxIsolation.WORKSPACE
    sandbox_enforcement: SandboxEnforcement = SandboxEnforcement.NONE
    remote_workspace: str | None = Field(default=None, exclude=True)
    assistant_message_id: str = Field(default="", exclude=True)
    input_files: tuple[str, ...] = ()
    identity: ExecutionIdentity | None = None
    memory_projection: str = Field(default="", exclude=True, repr=False)
    context_projection: str = Field(default="", exclude=True, repr=False)
    processed_input_paths: tuple[str, ...] = ()
    runtime_transport_factory: RuntimeTransportFactory | None = Field(
        default=None, exclude=True, repr=False
    )
    sandbox_command_executor: SandboxCommandExecutor | None = Field(
        default=None, exclude=True, repr=False
    )
    sandbox_file_plane: SandboxFilePlane | None = Field(default=None, exclude=True, repr=False)
    artifact_publisher: ArtifactPublisher | None = Field(default=None, exclude=True, repr=False)
    steering: SteeringInbox | None = Field(default=None, exclude=True, repr=False)
    resolved_policy: ResolvedPolicy | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="after")
    def derive_identity(self) -> "RuntimeContext":
        if not self.assistant_message_id:
            object.__setattr__(
                self,
                "assistant_message_id",
                f"assistant-{self.run.run_id}",
            )
        if self.identity is None:
            object.__setattr__(
                self,
                "identity",
                ExecutionIdentity(
                    tenant_id=self.session.tenant_id,
                    user_id=self.session.user_id,
                    agent_owner_user_id=self.session.resolved_agent_owner_user_id,
                    team_ids=self.session.team_ids,
                    project_id=self.session.agent_name,
                    session_id=self.session.session_id,
                    run_id=self.run.run_id,
                    agent_name=self.session.agent_name,
                    agent_version=self.session.agent_version,
                    connection_mode=self.session.connection_mode,
                ),
            )
        assert self.identity is not None
        if self.identity.tenant_id != self.run.tenant_id:
            raise ValueError("execution identity tenant does not match run")
        if self.identity.session_id != self.run.session_id:
            raise ValueError("execution identity session does not match run")
        return self


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentRuntime(Protocol):
    def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]: ...
