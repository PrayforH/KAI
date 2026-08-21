# Pyright stops control-flow analysis for the deliberately centralized `_execute`
# state machine once it exceeds its complexity budget, then misreports every
# symbol below that point as unused. Ruff remains the authoritative unused-code
# check for this module until the state machine is split into typed phases.
# pyright: reportUnusedImport=false, reportUnusedVariable=false, reportUnusedFunction=false

"""Coordinates one Run across repositories, Sandbox and Agent runtime."""

import asyncio
import hashlib
import logging
import mimetypes
import re
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AbstractContextManager, nullcontext, suppress
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar, cast
from uuid import uuid4

from harness.application.approvals import ApprovalService
from harness.application.artifacts import ArtifactService
from harness.application.events import EventService
from harness.application.input_artifacts import InputArtifactService
from harness.application.memory import UserMemoryService
from harness.application.runs import (
    RunQuotaPlan,
    RunQuotaPlanResolver,
    apply_environment_quota,
)
from harness.application.types import Clock
from harness.application.workspaces import (
    WorkspacePolicy,
    WorkspacePolicyResolver,
    WorkspaceService,
)
from harness.context.checkpoint import ContextCheckpointService
from harness.context.service import ContextService
from harness.core.errors import ConflictError
from harness.core.models import AgentRuntimeType, ExecutionIdentity, Run, RunStatus, Session
from harness.core.ports import CancellationWakeup, RunRepository, SessionRepository
from harness.core.state_machine import transition
from harness.deployments.boundaries import session_environment_policy
from harness.execution.credentials import CredentialLeaseError
from harness.observability.provider import Observability
from harness.observability.redaction import correlation_hash
from harness.policy.models import PolicyContext, PolicyDecision
from harness.policy.rules import PolicyEngine
from harness.policy.runtime import ResolvedPolicy
from harness.quota.repositories import QuotaExceededError
from harness.quota.service import QuotaService
from harness.reliability.metrics import ReliabilityMetrics
from harness.runtime.artifact_tools import ArtifactPublisher
from harness.runtime.audit_redaction import redact_text, redact_tool_arguments
from harness.runtime.base import (
    AgentRuntime,
    RuntimeContext,
    RuntimeEvent,
    RuntimeExecutionTimeoutError,
    RuntimeResultError,
)
from harness.runtime.input_redaction import (
    INPUT_CONTENT_REDACTION,
    INTERNAL_AGENT_ASSET_MARKER,
    INTERNAL_AGENT_ASSET_REDACTION,
    STAGED_INPUT_READ_MARKER,
    internal_agent_asset_access,
    redact_workspace_paths,
    staged_input_paths,
    staged_read_path,
)
from harness.runtime.mcp_credentials import McpCredentialError
from harness.runtime.subagent_governance import SubagentGovernanceError
from harness.runtime.tools import ToolResolutionError
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxHandle,
    SandboxIsolation,
    SandboxProvider,
)

RuntimeAssetStager = Callable[[str, str, str, str, Path], Awaitable[tuple[str, ...]]]
PolicyResolver = Callable[
    [str, str, str, str],
    Awaitable[PolicyEngine | ResolvedPolicy],
]
RunQualityHook = Callable[[Run, Session, str], Awaitable[object]]
RunCredentialRevoker = Callable[[str, str], Awaitable[None]]
SandboxResolver = Callable[[str, Session], Awaitable[SandboxProvider]]
T = TypeVar("T")
logger = logging.getLogger(__name__)
_MARKDOWN_ARTIFACT_PATH = re.compile(r"`([^`\r\n]+)`|\]\(([^)\r\n]+)\)")
_PLAIN_ARTIFACT_PATH = re.compile(
    r"(?<![\w/])((?:\.?/?[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.@+-]+\.[A-Za-z0-9]{1,12})"
)
_NON_DELIVERABLE_ROOTS = frozenset({".claude", ".git", ".harness-runtime", ".tmp", "inputs"})
_DEPENDENCY_ROOTS = frozenset({".cache", ".venv", "__pycache__", "node_modules", "vendor", "venv"})


class _RunCancellationRequestedError(RuntimeExecutionTimeoutError):
    """Internal control flow used to stop a long-running orchestration stage."""


def _bind_sandbox_command_executor(
    sandbox: SandboxProvider,
    handle: SandboxHandle,
) -> Callable[
    [Sequence[str], Mapping[str, str] | None, float],
    Awaitable[SandboxCommandResult],
]:
    async def execute(
        argv: Sequence[str],
        environment: Mapping[str, str] | None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        return await sandbox.execute(
            handle,
            argv,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )

    return execute


def read_runtime_artifact(
    workspace: Path, relative_path: str, *, max_bytes: int
) -> tuple[Path, bytes]:
    """Validate and bounded-read an untrusted runtime artifact."""
    candidate = workspace / relative_path
    try:
        artifact_path = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise ValueError("runtime artifact file does not exist") from None
    if not artifact_path.is_relative_to(workspace.resolve()):
        raise ValueError("runtime artifact path escaped the workspace")
    if candidate.is_symlink() or not artifact_path.is_file():
        raise ValueError("runtime artifact must be a regular file")
    if artifact_path.stat().st_size > max_bytes:
        raise ValueError("runtime artifact exceeds the output size limit")
    with artifact_path.open("rb") as stream:
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("runtime artifact exceeds the output size limit")
    return artifact_path, content


def final_artifact_paths(workspace: Path, response: str) -> tuple[str, ...]:
    """Resolve files explicitly declared by the final answer inside the workspace."""

    raw_candidates = [left or right for left, right in _MARKDOWN_ARTIFACT_PATH.findall(response)]
    raw_candidates.extend(_PLAIN_ARTIFACT_PATH.findall(response))
    resolved: list[str] = []
    root = workspace.resolve()
    for raw in dict.fromkeys(raw_candidates):
        value = raw.strip().strip("'\"").removeprefix("file://")
        if value.startswith("/workspace/"):
            value = value.removeprefix("/workspace/")
        value = value.removeprefix("./")
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0] in _NON_DELIVERABLE_ROOTS
        ):
            continue
        candidate = workspace.joinpath(*path.parts)
        try:
            artifact = candidate.resolve(strict=True)
        except (FileNotFoundError, RuntimeError):
            continue
        if artifact.is_relative_to(root) and artifact.is_file() and not candidate.is_symlink():
            resolved.append(path.as_posix())
    return tuple(resolved)


def terminal_runtime_result(event: object) -> bool:
    """Recognize an SDK result that cannot be followed by more model work."""
    if not isinstance(event, RuntimeEvent) or event.type != "runtime.result":
        return False
    payload = cast(dict[str, object], event.payload)
    return (
        payload.get("subtype") == "success"
        and payload.get("is_error") is not True
        and payload.get("stop_reason") == "end_turn"
    )


class RunOrchestrator:
    def __init__(
        self,
        *,
        sessions: SessionRepository,
        runs: RunRepository,
        events: EventService,
        runtime: AgentRuntime,
        sandbox: SandboxProvider,
        clock: Clock,
        policy: PolicyEngine | None = None,
        approvals: ApprovalService | None = None,
        workspaces: WorkspaceService | None = None,
        observability: Observability | None = None,
        artifacts: ArtifactService | None = None,
        input_artifacts: InputArtifactService | None = None,
        memory: UserMemoryService | None = None,
        workspace_policy_resolver: WorkspacePolicyResolver | None = None,
        runtime_asset_stager: RuntimeAssetStager | None = None,
        policy_resolver: PolicyResolver | None = None,
        output_artifact_max_bytes: int = 50 * 1024 * 1024,
        cancellation_poll_interval_seconds: float = 0.25,
        quality_hook: RunQualityHook | None = None,
        credential_revoker: RunCredentialRevoker | None = None,
        sandbox_resolver: SandboxResolver | None = None,
        quotas: QuotaService | None = None,
        quota_plan_resolver: RunQuotaPlanResolver | None = None,
        metrics: ReliabilityMetrics | None = None,
        cancellation_wakeup: CancellationWakeup | None = None,
        cancellation_wakeup_timeout_seconds: float = 30.0,
        context_checkpoints: ContextCheckpointService | None = None,
        context_service: ContextService | None = None,
    ) -> None:
        if cancellation_poll_interval_seconds <= 0:
            raise ValueError("cancellation poll interval must be positive")
        if cancellation_wakeup_timeout_seconds <= 0:
            raise ValueError("cancellation wakeup timeout must be positive")
        self._sessions = sessions
        self._runs = runs
        self._events = events
        self._runtime = runtime
        self._sandbox = sandbox
        self._clock = clock
        self._policy = policy
        self._approvals = approvals
        self._workspaces = workspaces
        self._observability = observability
        self._artifacts = artifacts
        self._input_artifacts = input_artifacts
        self._memory = memory
        self._workspace_policy_resolver = workspace_policy_resolver
        self._runtime_asset_stager = runtime_asset_stager
        self._policy_resolver = policy_resolver
        self._output_artifact_max_bytes = output_artifact_max_bytes
        self._cancellation_poll_interval_seconds = cancellation_poll_interval_seconds
        self._quality_hook = quality_hook
        self._credential_revoker = credential_revoker
        self._sandbox_resolver = sandbox_resolver
        self._quotas = quotas
        self._quota_plan_resolver = quota_plan_resolver
        self._metrics = metrics
        self._cancellation_wakeup = cancellation_wakeup
        self._cancellation_wakeup_timeout_seconds = cancellation_wakeup_timeout_seconds
        self._context_checkpoints = context_checkpoints
        self._context_service = context_service

    def _stage(
        self,
        name: str,
        attributes: Mapping[str, str | bool | int | float] | None = None,
    ) -> AbstractContextManager[None]:
        if self._observability is None:
            return nullcontext()
        return self._observability.span(name, attributes=attributes)

    def _record_visible_assistant_message(
        self,
        *,
        run_id: str,
        message_id: str,
        text: str,
    ) -> None:
        """Mirror public model progress into Langfuse without private reasoning."""
        if self._observability is None or not text.strip():
            return
        with self._observability.span(
            "assistant-progress",
            attributes={
                "run.id": run_id,
                "harness.message.id": message_id,
                "langfuse.observation.type": "event",
                "langfuse.observation.metadata.message_id": message_id,
            },
        ):
            self._observability.annotate_current_io(output_value=text)

    async def _runtime_events(self, context: RuntimeContext) -> AsyncIterator[Any]:
        with self._stage(
            "harness.runtime.execute",
            {
                "run.id": context.run.run_id,
                "agent.name": context.session.agent_name,
                "agent.version": context.session.agent_version,
            },
        ):
            async for event in self._runtime.execute(context):
                yield event

    async def _cancellable_runtime_events(
        self,
        context: RuntimeContext,
        *,
        on_event: Callable[[Any], None] | None = None,
    ) -> AsyncGenerator[Any, None]:
        """Poll durable cancellation while the SDK waits on background children."""
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event in self._runtime_events(context):
                    if on_event is not None:
                        on_event(event)
                    await queue.put(("event", event))
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - forwarded to orchestrator
                await queue.put(("error", error))
            else:
                await queue.put(("done", None))

        producer = asyncio.create_task(produce())
        try:
            while True:
                try:
                    kind, value = await self._await_cancellable(
                        context.run.tenant_id,
                        context.run.run_id,
                        queue.get(),
                    )
                except _RunCancellationRequestedError:
                    # An event can be produced just before the durable cancel is
                    # observed. Surface already-produced lifecycle facts before
                    # propagating cancellation so started children get terminals.
                    while not queue.empty():
                        queued_kind, queued_value = queue.get_nowait()
                        if queued_kind == "event":
                            yield queued_value
                    raise
                if kind == "done":
                    return
                if kind == "error":
                    if not isinstance(value, Exception):
                        raise RuntimeError("runtime producer returned an invalid error")
                    raise value
                yield value
                # Some compatible Claude endpoints emit the final ResultMessage
                # but keep the transport open. Waiting for another item leaves a
                # fully answered Run in RUNNING forever. An explicit successful
                # end_turn is the protocol boundary; cumulative/intermediate
                # results and provider errors continue through the normal path.
                if terminal_runtime_result(value):
                    return
        finally:
            if not producer.done():
                producer.cancel()
                # Deliver cancellation promptly, but do not put provider/SDK
                # shutdown latency on the durable user-cancellation path.
                # The producer no longer writes durable events after this
                # generator exits; sandbox destruction and credential
                # revocation below remain the authoritative hard stop.
                await asyncio.sleep(0)
            if producer.done():
                with suppress(asyncio.CancelledError):
                    await producer
            else:
                producer.add_done_callback(self._consume_background_task)

    @staticmethod
    def _consume_background_task(task: asyncio.Task[None]) -> None:
        """Observe a detached runtime shutdown so it cannot leak an exception."""
        with suppress(asyncio.CancelledError):
            task.exception()

    async def _fail_active_subagents(
        self,
        *,
        tenant_id: str,
        run: Run,
        error_code: str,
    ) -> None:
        """Give every started child a durable terminal before its parent terminates."""
        events = await self._events.list_after(tenant_id, run.run_id, 0)
        active: dict[str, dict[str, Any]] = {}
        for event in events:
            if not event.type.startswith("subagent."):
                continue
            task_id = event.payload.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                continue
            if event.type == "subagent.started":
                active[task_id] = event.payload
            elif event.type in {"subagent.completed", "subagent.failed"}:
                active.pop(task_id, None)
        for task_id, started in active.items():
            safe = {
                key: started[key]
                for key in (
                    "event_schema",
                    "alias",
                    "agent_name",
                    "agent_version",
                    "policy_profile",
                    "depth",
                    "parent_tool_use_id",
                )
                if key in started
            }
            await self._events.append(
                tenant_id=tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                event_type="subagent.failed",
                payload={
                    **safe,
                    "task_id": task_id,
                    "status": "cancelled",
                    "error_code": error_code,
                },
            )

    def _workspace_output_fingerprints(self, workspace: Path) -> dict[str, str]:
        output_root = workspace / "outputs"
        fingerprints: dict[str, str] = {}
        if output_root.is_dir():
            remaining = self._output_artifact_max_bytes
            for path in sorted(output_root.rglob("*")):
                if not (path.is_symlink() or path.is_file()):
                    continue
                if len(fingerprints) >= 100:
                    raise ValueError("workspace outputs exceed the artifact count limit")
                relative = path.relative_to(workspace).as_posix()
                _, content = read_runtime_artifact(
                    workspace,
                    relative,
                    max_bytes=remaining,
                )
                remaining -= len(content)
                fingerprints[relative] = hashlib.sha256(content).hexdigest()

        # Final prose may explicitly link a deliverable outside outputs/.
        # Snapshot existing files too, so mentioning an unchanged file from a
        # previous turn cannot republish it as a new Run artifact. This is a
        # bounded attribution scan, not a full workspace integrity pass.
        remaining = self._output_artifact_max_bytes
        for path in sorted(workspace.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative_path = path.relative_to(workspace)
            if (
                relative_path.parts[0] == "outputs"
                or any(part in _NON_DELIVERABLE_ROOTS for part in relative_path.parts)
                or any(part in _DEPENDENCY_ROOTS for part in relative_path.parts)
            ):
                continue
            if len(fingerprints) >= 1_000 or remaining <= 0:
                break
            size = path.stat().st_size
            if size > remaining:
                continue
            relative = relative_path.as_posix()
            _, content = read_runtime_artifact(
                workspace,
                relative,
                max_bytes=remaining,
            )
            remaining -= len(content)
            fingerprints[relative] = hashlib.sha256(content).hexdigest()
        return fingerprints

    async def _publish_workspace_outputs(
        self,
        *,
        tenant_id: str,
        run: Run,
        workspace: Path,
        baseline: Mapping[str, str],
        final_response: str,
        message_id: str | None = None,
    ) -> None:
        if self._artifacts is None:
            return
        candidates: dict[str, str] = {}
        output_root = workspace / "outputs"
        if output_root.is_dir():
            for path in output_root.rglob("*"):
                if path.is_symlink() or path.is_file():
                    candidates[path.relative_to(workspace).as_posix()] = "workspace-output"
        for relative in final_artifact_paths(workspace, final_response):
            candidates[relative] = "final-response"
        if len(candidates) > 100:
            raise ValueError("workspace artifacts exceed the artifact count limit")
        prior_events = await self._events.list_after(tenant_id, run.run_id, 0)
        published_paths = {
            str(event.payload["source_path"])
            for event in prior_events
            if event.type == "artifact.ready" and isinstance(event.payload.get("source_path"), str)
        }
        remaining = self._output_artifact_max_bytes
        for relative, source in sorted(candidates.items()):
            if relative in published_paths:
                continue
            resolved, content = read_runtime_artifact(
                workspace,
                relative,
                max_bytes=remaining,
            )
            if baseline.get(relative) == hashlib.sha256(content).hexdigest():
                continue
            remaining -= len(content)
            artifact = await self._artifacts.upload(
                tenant_id=tenant_id,
                run_id=run.run_id,
                name=relative.removeprefix("outputs/"),
                media_type=(mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"),
                content=content,
            )
            payload = artifact.model_dump(mode="json")
            payload["source"] = source
            payload["source_path"] = relative
            if message_id is not None:
                payload["message_id"] = message_id
            await self._events.append(
                tenant_id=tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                event_type="artifact.ready",
                payload=payload,
            )

    async def _recover_failed_workspace(
        self,
        *,
        tenant_id: str,
        run: Run,
        sandbox: SandboxProvider,
        handle: SandboxHandle | None,
        baseline: Mapping[str, str] | None,
        final_response: str,
        message_id: str | None,
        archive_on_complete: bool,
    ) -> bool:
        """Preserve artifacts and session state created before a runtime failure."""
        if handle is None or baseline is None:
            return True
        try:
            with self._stage("harness.sandbox.collect", {"run.id": run.run_id}):
                await sandbox.collect(handle)
        except Exception:  # noqa: BLE001 - preserve the primary runtime failure
            return False
        try:
            with self._stage(
                "harness.artifact.publish_outputs",
                {"run.id": run.run_id, "recovered": True},
            ):
                await self._publish_workspace_outputs(
                    tenant_id=tenant_id,
                    run=run,
                    workspace=handle.path,
                    baseline=baseline,
                    final_response=final_response,
                    message_id=message_id,
                )
        except Exception:  # noqa: BLE001 - preserve the primary runtime failure
            # A failed upload must not prevent the workspace snapshot below.
            pass
        if self._workspaces is None or not archive_on_complete:
            return True
        try:
            with self._stage(
                "harness.workspace.archive",
                {"run.id": run.run_id, "recovered": True},
            ):
                snapshot = await self._workspaces.archive(
                    tenant_id=tenant_id,
                    session_id=run.session_id,
                    workspace=handle.path,
                )
            await self._events.append(
                tenant_id=tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                event_type="workspace.archived",
                payload={
                    **snapshot.model_dump(mode="json"),
                    "recovered_from_failure": True,
                },
            )
        except Exception:  # noqa: BLE001 - preserve the primary runtime failure
            return False
        return True

    async def _await_cancellable(
        self,
        tenant_id: str,
        run_id: str,
        operation: Awaitable[T],
    ) -> T:
        """Await a long stage with low-latency hints and durable status checks."""
        task = asyncio.ensure_future(operation)
        notification: asyncio.Task[bool] | None = None
        observed_fencing_token = 0
        try:
            if self._cancellation_wakeup is not None:
                latest = await self._runs.get(tenant_id, run_id)
                observed_fencing_token = latest.fencing_token
                if latest.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    raise _RunCancellationRequestedError
            while True:
                if self._cancellation_wakeup is not None and notification is None:
                    notification = asyncio.create_task(
                        self._cancellation_wakeup.wait(
                            tenant_id,
                            run_id,
                            observed_fencing_token,
                            timeout_seconds=self._cancellation_wakeup_timeout_seconds,
                        )
                    )
                waiters: set[asyncio.Future[Any]] = {cast(asyncio.Future[Any], task)}
                if notification is not None:
                    waiters.add(cast(asyncio.Future[Any], notification))
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=self._cancellation_poll_interval_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done:
                    return await task
                wakeup_failed = False
                if notification is not None and notification in done:
                    try:
                        await notification
                    except Exception:
                        wakeup_failed = True
                        logger.debug(
                            "cancellation wakeup failed; using durable polling run_id=%s",
                            run_id,
                            exc_info=True,
                        )
                    notification = None
                latest = await self._runs.get(tenant_id, run_id)
                observed_fencing_token = max(
                    observed_fencing_token,
                    latest.fencing_token,
                )
                if latest.status in {RunStatus.CANCELLING, RunStatus.CANCELLED}:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    raise _RunCancellationRequestedError
                if wakeup_failed:
                    # Avoid a hot Redis reconnect loop while retaining the
                    # existing bounded PostgreSQL fallback.
                    done, _ = await asyncio.wait(
                        {task},
                        timeout=self._cancellation_poll_interval_seconds,
                    )
                    if task in done:
                        return await task
        finally:
            if notification is not None and not notification.done():
                notification.cancel()
                with suppress(asyncio.CancelledError):
                    await notification
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def _move(
        self,
        current: Run,
        target: RunStatus,
        *,
        error_code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Run:
        next_status = transition(current.status, target)
        updated = current.model_copy(
            update={
                "status": next_status,
                "updated_at": self._clock(),
                "fencing_token": current.fencing_token + 1,
                "error_code": error_code,
            }
        )
        if not await self._runs.compare_and_set(current.status, updated):
            raise ConflictError(f"stale worker attempted to update run: {current.run_id}")
        event_payload = dict(payload or {})
        if error_code is not None:
            event_payload.setdefault("error_code", error_code)
        await self._events.append(
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            session_id=current.session_id,
            event_type=f"run.{target.value}",
            payload=event_payload,
        )
        if (
            self._metrics is not None
            and current.status is RunStatus.CANCELLING
            and target is RunStatus.CANCELLED
        ):
            self._metrics.observe(
                "harness_workflow_convergence_seconds",
                max(0, (updated.updated_at - current.updated_at).total_seconds()),
                labels={"workflow": "run.cancel"},
            )
        return updated

    def _observe_run_stage(self, stage: str, duration_seconds: float) -> None:
        if self._metrics is None:
            return
        self._metrics.observe(
            "harness_run_stage_duration_seconds",
            max(0, duration_seconds),
            labels={"stage": stage},
        )

    async def _handle_unexpected_error(
        self,
        tenant_id: str,
        run_id: str,
        error: Exception,
    ) -> Run:
        latest = await self._runs.get(tenant_id, run_id)
        if latest.status is RunStatus.CANCELLING:
            return await self._move(latest, RunStatus.CANCELLED)
        if latest.status is RunStatus.CANCELLED:
            return latest
        if isinstance(error, ConflictError) and not isinstance(error, QuotaExceededError):
            # Only ownership/fencing conflicts prove that another executor now
            # owns the Run. Other domain conflicts (quota finalization,
            # artifacts, approval state, etc.) are real failures; silently
            # returning a non-terminal Run leaves the UI spinning forever.
            ownership_conflict = str(error).startswith(
                (
                    "stale worker attempted to update run:",
                    "run ownership changed while reclaiming:",
                    "run is already owned or paused:",
                )
            )
            if ownership_conflict:
                return latest
        if latest.status.is_terminal:
            return latest
        await self._fail_active_subagents(
            tenant_id=tenant_id,
            run=latest,
            error_code="parent_failed",
        )
        logger.error(
            "run execution failed run_id=%s error_type=%s message=%s",
            run_id,
            type(error).__name__,
            redact_text(str(error), limit=400),
        )
        payload = {"error_type": type(error).__name__}
        if isinstance(
            error,
            (
                ConflictError,
                ToolResolutionError,
                SubagentGovernanceError,
                CredentialLeaseError,
                McpCredentialError,
            ),
        ):
            payload["message"] = str(error)
        return await self._move(
            latest,
            RunStatus.FAILED,
            error_code=(
                f"quota_exceeded_{error.resource.value}"
                if isinstance(error, QuotaExceededError)
                else "runtime_error"
            ),
            payload=payload,
        )

    async def _reclaim(self, current: Run) -> Run:
        reclaimed = current.model_copy(
            update={
                "updated_at": self._clock(),
                "fencing_token": current.fencing_token + 1,
            }
        )
        if not await self._runs.compare_and_set(current.status, reclaimed):
            raise ConflictError(f"run ownership changed while reclaiming: {current.run_id}")
        await self._events.append(
            tenant_id=current.tenant_id,
            run_id=current.run_id,
            session_id=current.session_id,
            event_type="run.recovered",
            payload={"from_status": current.status.value},
        )
        return reclaimed

    async def _ensure_quota_admission(self, tenant_id: str, run_id: str, session: Session) -> None:
        if self._quotas is None:
            return
        plan = (
            await self._quota_plan_resolver(
                tenant_id,
                session.resolved_agent_owner_user_id,
                session.agent_name,
                session.agent_version,
            )
            if self._quota_plan_resolver is not None
            else RunQuotaPlan(None, None, 3600)
        )
        plan = apply_environment_quota(plan, session)
        await self._quotas.ensure_run_admitted(
            tenant_id=tenant_id,
            run_id=run_id,
            user_id=session.user_id,
            team_ids=session.team_ids,
            api_key_id=session.api_key_id,
            agent_name=session.agent_name,
            environment=session.environment,
            max_budget_usd=plan.max_budget_usd,
            max_model_tokens=plan.max_model_tokens,
            ttl_seconds=plan.ttl_seconds,
        )

    async def _record_quota_result(
        self,
        tenant_id: str,
        run_id: str,
        session: Session,
        payload: Mapping[str, Any],
    ) -> None:
        if self._quotas is None:
            return
        raw_usage = payload.get("usage")
        await self._quotas.record_run_result(
            tenant_id=tenant_id,
            run_id=run_id,
            user_id=session.user_id,
            team_ids=session.team_ids,
            api_key_id=session.api_key_id,
            agent_name=session.agent_name,
            environment=session.environment,
            usage=(cast(dict[str, object], raw_usage) if isinstance(raw_usage, dict) else None),
            total_cost_usd=payload.get("total_cost_usd"),
        )

    async def _release_terminal_quota(self, result: Run) -> None:
        if result.status.is_terminal and self._quotas is not None:
            await self._quotas.release_subject(result.tenant_id, result.run_id)

    async def _checkpoint_context(
        self,
        *,
        session: Session,
        run: Run,
        final_response: str,
    ) -> None:
        if self._context_checkpoints is None:
            return
        try:
            context_events = await self._events.list_after(
                run.tenant_id,
                run.run_id,
                0,
            )
            digest = await self._context_checkpoints.checkpoint_run(
                session=session,
                run=run,
                events=context_events,
                final_response=final_response,
            )
            if digest is None or any(
                event.type == "context.digest.created"
                and event.payload.get("digest_id") == digest.digest_id
                for event in context_events
            ):
                return
            await self._events.append(
                tenant_id=run.tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                event_type="context.digest.created",
                payload={
                    "digest_id": digest.digest_id,
                    "version": digest.version,
                    "content_hash": digest.content_hash,
                    "transcript_checkpoint_hash": (digest.source.transcript_checkpoint_hash),
                    "through_event_sequence": digest.source.through_event_sequence,
                    "trust_high_watermark": digest.trust_high_watermark.value,
                },
            )
        except Exception as error:  # noqa: BLE001 - answer is already durable
            logger.warning(
                "context checkpoint failed after successful runtime run_id=%s error_type=%s",
                run.run_id,
                type(error).__name__,
            )
            with suppress(Exception):
                await self._events.append(
                    tenant_id=run.tenant_id,
                    run_id=run.run_id,
                    session_id=run.session_id,
                    event_type="context.digest.failed",
                    payload={
                        "error_code": "context_checkpoint_failed",
                        "error_type": type(error).__name__,
                    },
                )

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        if self._observability is None:
            result = await self._execute(tenant_id, run_id)
            await self._release_terminal_quota(result)
            return result
        run = await self._runs.get(tenant_id, run_id)
        session = await self._sessions.get(tenant_id, run.session_id)
        correlation_attributes: dict[str, str] = {
            "langfuse.session.id": run.session_id,
            "langfuse.trace.name": "agent-run",
            "langfuse.user.id": correlation_hash(session.user_id),
            "langfuse.trace.metadata.run_id": run.run_id,
            "langfuse.trace.metadata.agent_name": session.agent_name,
            "langfuse.trace.metadata.agent_version": session.agent_version,
            "session.id": run.session_id,
            "agent.name": session.agent_name,
            "agent.version": session.agent_version,
        }
        if session.deployment_snapshot_id:
            correlation_attributes["deployment.snapshot.id"] = session.deployment_snapshot_id
        if session.environment:
            correlation_attributes["deployment.environment"] = session.environment
            correlation_attributes["langfuse.trace.metadata.environment"] = session.environment
        environment_policy = session_environment_policy(session)
        if environment_policy is not None:
            correlation_attributes["environment.policy.hash"] = environment_policy.policy_hash
            correlation_attributes["environment.policy.revision"] = str(
                environment_policy.policy_revision
            )
        eval_run_id = run.input.get("eval_run_id")
        if isinstance(eval_run_id, str) and eval_run_id:
            correlation_attributes["eval.run.id"] = eval_run_id
        with self._observability.bind_attributes(correlation_attributes):
            with self._observability.span(
                "harness.worker.run",
                carrier=run.trace_context,
                attributes={
                    "run.id": run_id,
                    "tenant.hash": correlation_hash(tenant_id),
                    "langfuse.observation.type": "agent",
                    "langfuse.observation.metadata.agent_name": session.agent_name,
                    "langfuse.version": session.agent_version,
                },
            ):
                prompt = run.input.get("prompt")
                self._observability.annotate_current_io(input_value=prompt)
                self._observability.annotate_current_io(
                    input_value=prompt,
                    trace_level=True,
                )
                result = await self._execute(tenant_id, run_id)
                self._observability.annotate_current_span(
                    {
                        "langfuse.observation.level": (
                            "ERROR" if result.status is RunStatus.FAILED else "DEFAULT"
                        ),
                        "langfuse.observation.status_message": result.status.value,
                    }
                )
                await self._release_terminal_quota(result)
                trace_id = self._observability.current_trace_id()
                if result.status.is_terminal and self._quality_hook is not None:
                    try:
                        await self._quality_hook(result, session, trace_id or "")
                    except Exception:
                        # Quality export is fail-open for the Agent Run. Durable
                        # sync state and alerts are reconciled independently.
                        pass
                return result

    async def _execute(  # pyright: ignore[reportGeneralTypeIssues]
        self, tenant_id: str, run_id: str
    ) -> Run:
        run = await self._runs.get(tenant_id, run_id)
        if run.status.is_terminal:
            return run
        if run.status is RunStatus.CANCELLING:
            return await self._move(run, RunStatus.CANCELLED)
        if run.status is RunStatus.WAITING_APPROVAL:
            # A recovered lease can observe a Run whose original SDK hook is
            # still waiting inline. Leave the durable approval owner in place;
            # the queue lease can be acknowledged without starting a duplicate.
            return run
        is_resume = run.status is RunStatus.RUNNING
        is_provision_recovery = run.status is RunStatus.PROVISIONING
        if run.status is not RunStatus.QUEUED and not is_resume and not is_provision_recovery:
            raise ConflictError(f"run is already owned or paused: {run_id} ({run.status.value})")

        handle: SandboxHandle | None = None
        active_sandbox = self._sandbox
        output_baseline: Mapping[str, str] | None = None
        final_response_text = ""
        final_response_message_id: str | None = None
        workspace_policy = WorkspacePolicy()
        workspace_durable = True
        latest_runtime_result_payload: Mapping[str, Any] | None = None
        provisioning_started_at = None

        try:
            if not is_resume:
                if is_provision_recovery:
                    run = await self._reclaim(run)
                else:
                    run = await self._move(run, RunStatus.PROVISIONING)
                    provisioning_started_at = run.updated_at
                    self._observe_run_stage(
                        "queue_wait",
                        (run.updated_at - run.created_at).total_seconds(),
                    )
            else:
                run = await self._reclaim(run)
            session = await self._sessions.get(tenant_id, run.session_id)
            if self._sandbox_resolver is not None:
                active_sandbox = await self._sandbox_resolver(tenant_id, session)
            await self._ensure_quota_admission(tenant_id, run_id, session)
            with self._stage("harness.sandbox.provision", {"run.id": run_id}):
                handle = await self._await_cancellable(
                    tenant_id,
                    run_id,
                    active_sandbox.provision(run),
                )
            policy_resolution = (
                await self._policy_resolver(
                    tenant_id,
                    session.resolved_agent_owner_user_id,
                    session.agent_name,
                    session.agent_version,
                )
                if self._policy_resolver is not None
                else self._policy
            )
            resolved_policy = (
                policy_resolution if isinstance(policy_resolution, ResolvedPolicy) else None
            )
            active_policy = (
                policy_resolution.call_policy
                if isinstance(policy_resolution, ResolvedPolicy)
                else policy_resolution
            )
            workspace_policy = (
                await self._workspace_policy_resolver(
                    tenant_id,
                    session.resolved_agent_owner_user_id,
                    session.agent_name,
                    session.agent_version,
                )
                if self._workspace_policy_resolver is not None
                else WorkspacePolicy()
            )
            if self._workspaces is not None and workspace_policy.restore_session:
                with self._stage("harness.workspace.restore", {"run.id": run_id}):
                    restored = await self._workspaces.restore_latest(
                        tenant_id=tenant_id,
                        session_id=session.session_id,
                        workspace=handle.path,
                    )
                if restored is not None:
                    await self._events.append(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        session_id=run.session_id,
                        event_type="workspace.restored",
                        payload=restored.model_dump(mode="json"),
                    )
            identity = ExecutionIdentity(
                tenant_id=session.tenant_id,
                user_id=session.user_id,
                agent_owner_user_id=session.resolved_agent_owner_user_id,
                team_ids=session.team_ids,
                project_id=session.agent_name,
                session_id=session.session_id,
                run_id=run.run_id,
                agent_name=session.agent_name,
                agent_version=session.agent_version,
                connection_mode=session.connection_mode,
            )
            with self._stage("harness.memory.load", {"run.id": run_id}):
                memory_projection = (
                    await self._memory.projection(identity) if self._memory is not None else ""
                )
            raw_input_artifact_ids: object = run.input.get("input_artifact_ids", [])
            if not isinstance(raw_input_artifact_ids, list):
                raise ValueError("run input_artifact_ids must be a list of strings")
            input_artifact_ids: list[str] = []
            for item in cast(list[object], raw_input_artifact_ids):
                if not isinstance(item, str):
                    raise ValueError("run input_artifact_ids must be a list of strings")
                input_artifact_ids.append(item)
            if input_artifact_ids and self._input_artifacts is None:
                raise RuntimeError("input artifact service is not configured")
            with self._stage(
                "harness.input.process",
                {"run.id": run_id, "input.count": len(input_artifact_ids)},
            ):
                staged_inputs = (
                    await self._input_artifacts.stage_for_run(
                        tenant_id=tenant_id,
                        user_id=session.user_id,
                        input_artifact_ids=input_artifact_ids,
                        workspace=handle.path,
                        identity=identity,
                    )
                    if self._input_artifacts is not None
                    else []
                )
            for staged in staged_inputs:
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    event_type="input.staged",
                    payload=staged.model_dump(mode="json"),
                )
            input_paths = staged_input_paths(
                handle.path,
                tuple(staged.path for staged in staged_inputs),
            )
            if self._runtime_asset_stager is not None:
                with self._stage("harness.agent.assets.stage", {"run.id": run_id}):
                    staged_skills = await self._runtime_asset_stager(
                        tenant_id,
                        session.resolved_agent_owner_user_id,
                        session.agent_name,
                        session.agent_version,
                        handle.path,
                    )
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    event_type="agent.assets.staged",
                    payload={"skills": list(staged_skills)},
                )
            with self._stage("harness.sandbox.prepare", {"run.id": run_id}):
                await self._await_cancellable(
                    tenant_id,
                    run_id,
                    active_sandbox.prepare(handle),
                )
            workspace_durable = False
            staged_read_tool_calls: set[str] = set()
            internal_asset_tool_calls: set[str] = set()
            if not is_resume:
                run = await self._move(run, RunStatus.RUNNING)
                if provisioning_started_at is not None:
                    self._observe_run_stage(
                        "environment_prepare",
                        (run.updated_at - provisioning_started_at).total_seconds(),
                    )
            else:
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    event_type="run.resumed",
                )

            async def sync_workspace() -> None:
                await active_sandbox.collect(handle)

            artifact_publisher = (
                ArtifactPublisher(
                    workspace=handle.path,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    artifacts=self._artifacts,
                    events=self._events,
                    sync_workspace=sync_workspace,
                    max_file_bytes=self._output_artifact_max_bytes,
                    observability=self._observability,
                )
                if self._artifacts is not None
                else None
            )

            context_projection = (
                await self._context_service.recovery_projection(
                    tenant_id,
                    session.user_id,
                    session.session_id,
                )
                if (
                    self._context_service is not None
                    and session.resolved_runtime_thread_id is None
                )
                else ""
            )
            if context_projection:
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    event_type="context.recovery.loaded",
                    payload={"mode": "digest_rebase"},
                )
            context = RuntimeContext(
                run=run,
                session=session,
                workspace=handle.path,
                sandbox_provider=handle.provider,
                sandbox_isolation=handle.isolation_level,
                remote_workspace=handle.remote_workspace,
                assistant_message_id=f"assistant-{run_id}-{uuid4().hex}",
                input_files=tuple(
                    path for item in staged_inputs for path in (item.path, *item.processed_paths)
                ),
                identity=identity,
                memory_projection=memory_projection,
                context_projection=context_projection,
                processed_input_paths=tuple(
                    path for item in staged_inputs for path in item.processed_paths
                ),
                runtime_transport_factory=handle.runtime_transport_factory,
                sandbox_command_executor=(
                    _bind_sandbox_command_executor(active_sandbox, handle)
                    if handle.deferred_tool_execution or handle.provider == "local"
                    else None
                ),
                artifact_publisher=artifact_publisher,
                resolved_policy=resolved_policy,
            )
            if resolved_policy is not None:
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    event_type="policy.resolved",
                    payload={
                        "policy_id": resolved_policy.policy_id,
                        "revision": resolved_policy.revision,
                        "content_hash": resolved_policy.content_hash,
                    },
                )
            output_baseline = self._workspace_output_fingerprints(handle.path)
            active_message_id: str | None = context.assistant_message_id
            last_completed_message_id: str | None = None
            active_message_text = ""
            known_tool_calls: dict[str, tuple[bool, bool]] = {}
            runtime_started_at = self._clock()
            first_runtime_event_observed = False
            first_provider_event_observed = False
            first_runtime_text_observed = False

            def observe_runtime_event(runtime_event: Any) -> None:
                nonlocal first_runtime_event_observed
                nonlocal first_provider_event_observed
                nonlocal first_runtime_text_observed
                observed_at = self._clock()
                elapsed = (observed_at - runtime_started_at).total_seconds()
                event_type = getattr(runtime_event, "type", None)
                if not first_runtime_event_observed:
                    self._observe_run_stage("runtime_first_event", elapsed)
                    first_runtime_event_observed = True
                if not first_provider_event_observed and event_type not in {
                    "model.route.selected",
                    "tool.directory.loaded",
                    "tool.directory.degraded",
                }:
                    self._observe_run_stage("provider_first_event", elapsed)
                    first_provider_event_observed = True
                if (
                    not first_runtime_text_observed
                    and event_type == "message.delta"
                    and str(getattr(runtime_event, "payload", {}).get("text", "")).strip()
                ):
                    self._observe_run_stage("runtime_first_text", elapsed)
                    first_runtime_text_observed = True

            runtime_events = self._cancellable_runtime_events(
                context,
                on_event=observe_runtime_event,
            )
            async for runtime_event in runtime_events:
                latest = await self._runs.get(tenant_id, run_id)
                if latest.status is RunStatus.CANCELLING:
                    if runtime_event.type == "subagent.started":
                        await self._events.append(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            session_id=run.session_id,
                            event_type=runtime_event.type,
                            payload=dict(runtime_event.payload),
                        )
                    await runtime_events.aclose()
                    await self._fail_active_subagents(
                        tenant_id=tenant_id,
                        run=latest,
                        error_code="parent_cancelled",
                    )
                    return await self._move(latest, RunStatus.CANCELLED)
                if latest.status.is_terminal:
                    await runtime_events.aclose()
                    return latest
                run = latest
                payload = dict(runtime_event.payload)
                if runtime_event.type == "runtime.session.recovered":
                    previous_session_id = payload.get("previous_session_id")
                    if not isinstance(previous_session_id, str) or not previous_session_id:
                        raise ValueError("session recovery event is missing previous_session_id")
                    raw_runtime_type = payload.get("runtime", session.runtime_type)
                    if raw_runtime_type not in {"claude-agent-sdk", "codex-app-server"}:
                        raise ValueError("session recovery event has an invalid runtime")
                    runtime_type = cast(AgentRuntimeType, raw_runtime_type)
                    session = await self._sessions.clear_runtime_thread(
                        tenant_id,
                        session.session_id,
                        runtime_type,
                        previous_session_id,
                    )
                if runtime_event.type == "runtime.result":
                    # The SDK can emit more than one cumulative ResultMessage
                    # around an inline approval/background-task continuation.
                    # Charging each snapshot reuses the same quota idempotency
                    # key with a different amount. Keep the latest cumulative
                    # result and settle it once when the SDK stream closes.
                    latest_runtime_result_payload = payload
                if runtime_event.type in {"runtime.system", "runtime.result"}:
                    sdk_session_id = payload.get("session_id")
                    if isinstance(sdk_session_id, str) and sdk_session_id:
                        session = await self._sessions.bind_runtime_thread(
                            tenant_id,
                            session.session_id,
                            "claude-agent-sdk",
                            sdk_session_id,
                        )
                if runtime_event.type == "runtime.thread.started":
                    runtime_thread_id = payload.get("thread_id")
                    raw_runtime_type = payload.get("runtime")
                    if not isinstance(runtime_thread_id, str) or not runtime_thread_id:
                        raise ValueError("runtime thread event is missing thread_id")
                    if raw_runtime_type not in {"claude-agent-sdk", "codex-app-server"}:
                        raise ValueError("runtime thread event has an invalid runtime")
                    session = await self._sessions.bind_runtime_thread(
                        tenant_id,
                        session.session_id,
                        cast(AgentRuntimeType, raw_runtime_type),
                        runtime_thread_id,
                    )
                original_tool_arguments: dict[str, Any] | None = None
                if runtime_event.type == "tool.request":
                    raw_arguments = payload.get("arguments")
                    if isinstance(raw_arguments, dict):
                        original_tool_arguments = dict(cast(dict[str, Any], raw_arguments))
                    relative_input_path = staged_read_path(
                        payload,
                        workspace=handle.path,
                        staged_paths=input_paths,
                    )
                    if relative_input_path is not None:
                        tool_call_id = str(payload.get("tool_call_id", ""))
                        if tool_call_id:
                            staged_read_tool_calls.add(tool_call_id)
                        sanitized_arguments = dict(original_tool_arguments or {})
                        sanitized_arguments["file_path"] = relative_input_path
                        payload["arguments"] = sanitized_arguments
                        payload[STAGED_INPUT_READ_MARKER] = True
                    if internal_agent_asset_access(payload):
                        tool_call_id = str(payload.get("tool_call_id", ""))
                        if tool_call_id:
                            internal_asset_tool_calls.add(tool_call_id)
                        payload[INTERNAL_AGENT_ASSET_MARKER] = True
                    tool_call_id = str(payload.get("tool_call_id", ""))
                    if tool_call_id:
                        known_tool_calls[tool_call_id] = (
                            tool_call_id in staged_read_tool_calls,
                            tool_call_id in internal_asset_tool_calls,
                        )
                elif runtime_event.type == "tool.result":
                    tool_call_id = str(payload.get("tool_call_id", ""))
                    known_redaction = known_tool_calls.get(tool_call_id)
                    redact_result, redact_internal_asset = known_redaction or (False, False)
                    if known_redaction is None and tool_call_id:
                        prior_events = await self._events.list_after(
                            tenant_id,
                            run_id,
                            0,
                        )
                        matching_request = next(
                            (
                                event
                                for event in reversed(prior_events)
                                if event.type == "tool.request"
                                and str(event.payload.get("tool_call_id", "")) == tool_call_id
                            ),
                            None,
                        )
                        redact_result = bool(
                            matching_request
                            and matching_request.payload.get(STAGED_INPUT_READ_MARKER)
                        )
                        redact_internal_asset = bool(
                            matching_request
                            and (
                                matching_request.payload.get(INTERNAL_AGENT_ASSET_MARKER)
                                or internal_agent_asset_access(matching_request.payload)
                            )
                        )
                    if redact_result:
                        payload["content"] = INPUT_CONTENT_REDACTION
                        payload["redacted"] = True
                    elif redact_internal_asset:
                        payload["content"] = INTERNAL_AGENT_ASSET_REDACTION
                        payload["redacted"] = True
                        payload["redaction_reason"] = "internal_agent_asset"
                payload = cast(
                    dict[str, Any],
                    redact_workspace_paths(payload, handle.path),
                )
                if runtime_event.type == "message.start":
                    active_message_text = ""
                    active_message_id = str(
                        payload.get("message_id")
                        or active_message_id
                        or f"assistant-{run_id}-{uuid4().hex}"
                    )
                    payload["message_id"] = active_message_id
                elif runtime_event.type in {"message.delta", "message.completed"}:
                    active_message_id = str(
                        payload.get("message_id")
                        or active_message_id
                        or f"assistant-{run_id}-{uuid4().hex}"
                    )
                    payload["message_id"] = active_message_id
                    if runtime_event.type == "message.delta":
                        active_message_text += str(payload.get("text", ""))
                    else:
                        final_response_text = active_message_text
                        final_response_message_id = active_message_id
                        last_completed_message_id = active_message_id
                        self._record_visible_assistant_message(
                            run_id=run_id,
                            message_id=active_message_id,
                            text=active_message_text,
                        )
                elif runtime_event.type == "tool.request":
                    parent_message_id = active_message_id or last_completed_message_id
                    if parent_message_id is not None:
                        payload["message_id"] = parent_message_id
                if runtime_event.type == "tool.request":
                    audit_arguments = payload.get("arguments")
                    if isinstance(audit_arguments, dict):
                        payload["arguments"] = redact_tool_arguments(
                            str(payload.get("name", "")),
                            cast(dict[str, Any], audit_arguments),
                        )
                if runtime_event.type == "artifact.output" and self._artifacts is not None:
                    if handle.isolation_level is SandboxIsolation.CONTAINER:
                        await active_sandbox.collect(handle)
                    relative_path = str(payload.get("path", ""))
                    artifact_path, artifact_content = read_runtime_artifact(
                        handle.path,
                        relative_path,
                        max_bytes=self._output_artifact_max_bytes,
                    )
                    with self._stage("harness.artifact.publish", {"run.id": run_id}):
                        artifact = await self._artifacts.upload(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            name=str(payload.get("name", artifact_path.name)),
                            media_type=str(payload.get("media_type", "application/octet-stream")),
                            content=artifact_content,
                        )
                    artifact_payload = artifact.model_dump(mode="json")
                    artifact_payload["source_path"] = relative_path
                    artifact_message_id = active_message_id or last_completed_message_id
                    if artifact_message_id is not None:
                        artifact_payload["message_id"] = artifact_message_id
                    await self._events.append(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        session_id=run.session_id,
                        event_type="artifact.ready",
                        payload=artifact_payload,
                    )
                    continue
                if runtime_event.type == "tool.request" and active_policy is not None:
                    tool_name = str(payload.get("name", ""))
                    tool_call_id = str(payload.get("tool_call_id", ""))
                    arguments = original_tool_arguments
                    if arguments is None:
                        arguments = payload.get("arguments", {})
                    if not isinstance(arguments, dict):
                        arguments = {}
                    typed_arguments = cast(dict[str, Any], arguments)
                    # Keep the request in the durable event stream so AG-UI can
                    # pair progress and results even when policy makes the decision.
                    await self._events.append(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        session_id=run.session_id,
                        event_type="tool.request",
                        payload=payload,
                    )
                    result = active_policy.evaluate(
                        PolicyContext(
                            tenant_id=tenant_id,
                            agent_name=session.agent_name,
                            tool_name=tool_name,
                            arguments=typed_arguments,
                        )
                    )
                    if result.decision is PolicyDecision.DENY:
                        await self._events.append(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            session_id=run.session_id,
                            event_type="tool.result",
                            payload={
                                "tool_call_id": tool_call_id,
                                "is_error": True,
                                "error": {
                                    "code": "policy_denied",
                                    "message": result.reason,
                                    "rule": result.rule_name,
                                },
                            },
                        )
                        continue
                    if result.decision is PolicyDecision.ASK:
                        if self._approvals is None:
                            raise RuntimeError("approval service is not configured")
                        approval = await self._approvals.request(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            tool_call_id=tool_call_id,
                            reason=result.reason,
                            message_id=active_message_id or last_completed_message_id,
                            tool_name=tool_name,
                            argument_summary=cast(
                                dict[str, Any],
                                payload.get("arguments", {}),
                            ),
                            sandbox_provider=handle.provider,
                            sandbox_isolation=handle.isolation_level.value,
                            policy_rule=result.rule_name,
                            risk=("high" if tool_name == "Bash" else "medium"),
                        )
                        if approval.status.value == "pending":
                            if active_message_id is not None:
                                await self._events.append(
                                    tenant_id=tenant_id,
                                    run_id=run_id,
                                    session_id=run.session_id,
                                    event_type="message.completed",
                                    payload={"message_id": active_message_id},
                                )
                            await runtime_events.aclose()
                            return await self._runs.get(tenant_id, run_id)
                    await self._events.append(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        session_id=run.session_id,
                        event_type="tool.allowed",
                        payload={
                            "tool_call_id": tool_call_id,
                            "policy_rule": result.rule_name,
                        },
                    )
                    continue
                await self._events.append(
                    tenant_id=tenant_id,
                    run_id=run_id,
                    session_id=run.session_id,
                    event_type=runtime_event.type,
                    payload=payload,
                )
                if runtime_event.type == "message.completed":
                    active_message_id = None
            if latest_runtime_result_payload is not None:
                quota_payload = latest_runtime_result_payload
                latest_runtime_result_payload = None
                await self._record_quota_result(
                    tenant_id,
                    run_id,
                    session,
                    quota_payload,
                )
            with self._stage("harness.sandbox.collect", {"run.id": run_id}):
                await active_sandbox.collect(handle)
            with self._stage("harness.artifact.publish_outputs", {"run.id": run_id}):
                await self._publish_workspace_outputs(
                    tenant_id=tenant_id,
                    run=run,
                    workspace=handle.path,
                    baseline=output_baseline,
                    final_response=final_response_text,
                    message_id=final_response_message_id,
                )
            if self._workspaces is not None and workspace_policy.archive_on_complete:
                try:
                    with self._stage("harness.workspace.archive", {"run.id": run_id}):
                        snapshot = await self._workspaces.archive(
                            tenant_id=tenant_id,
                            session_id=run.session_id,
                            workspace=handle.path,
                        )
                    await self._events.append(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        session_id=run.session_id,
                        event_type="workspace.archived",
                        payload=snapshot.model_dump(mode="json"),
                    )
                    workspace_durable = True
                except Exception as error:  # noqa: BLE001 - model result is already durable
                    workspace_durable = False
                    logger.warning(
                        "workspace archive failed after successful runtime "
                        "run_id=%s error_type=%s message=%s",
                        run_id,
                        type(error).__name__,
                        error,
                    )
                    await self._events.append(
                        tenant_id=tenant_id,
                        run_id=run_id,
                        session_id=run.session_id,
                        event_type="workspace.archive.failed",
                        payload={
                            "error_code": "workspace_archive_failed",
                            "error_type": type(error).__name__,
                        },
                    )
            else:
                workspace_durable = True
            await self._checkpoint_context(
                session=session,
                run=run,
                final_response=final_response_text,
            )
            latest = await self._runs.get(tenant_id, run_id)
            if latest.status is RunStatus.CANCELLING:
                return await self._move(latest, RunStatus.CANCELLED)
            if latest.status.is_terminal:
                return latest
            if self._observability is not None:
                self._observability.annotate_current_io(output_value=final_response_text)
                self._observability.annotate_current_io(
                    output_value=final_response_text,
                    trace_level=True,
                )
            return await self._move(latest, RunStatus.SUCCEEDED)
        except _RunCancellationRequestedError:
            # Cancellation is an explicit request to stop, not a failed Run
            # whose partial workspace must be synchronously recovered. The
            # The runtime producer has already received cancellation before
            # this boundary. Provider/SDK shutdown may finish in the background;
            # persist child terminals and the parent terminal immediately while
            # credential revocation and sandbox destruction still run in
            # ``finally``. This keeps provider cleanup and MinIO/archive latency
            # out of the cancellation SLO and deliberately discards partial
            # workspace mutations from the cancelled Run.
            workspace_durable = True
            latest = await self._runs.get(tenant_id, run_id)
            if latest.status is RunStatus.CANCELLING:
                await self._fail_active_subagents(
                    tenant_id=tenant_id,
                    run=latest,
                    error_code="parent_cancelled",
                )
                return await self._move(latest, RunStatus.CANCELLED)
            if latest.status.is_terminal:
                return latest
            raise
        except RuntimeExecutionTimeoutError:
            workspace_durable = await self._recover_failed_workspace(
                tenant_id=tenant_id,
                run=run,
                sandbox=active_sandbox,
                handle=handle,
                baseline=output_baseline,
                final_response=final_response_text,
                message_id=final_response_message_id,
                archive_on_complete=workspace_policy.archive_on_complete,
            )
            latest = await self._runs.get(tenant_id, run_id)
            if latest.status is RunStatus.CANCELLING:
                await self._fail_active_subagents(
                    tenant_id=tenant_id,
                    run=latest,
                    error_code="parent_cancelled",
                )
                return await self._move(latest, RunStatus.CANCELLED)
            if latest.status.is_terminal:
                return latest
            await self._fail_active_subagents(
                tenant_id=tenant_id,
                run=latest,
                error_code="parent_timed_out",
            )
            return await self._move(
                latest,
                RunStatus.TIMED_OUT,
                error_code="runtime_timeout",
                payload={"timeout": "manifest runtime limit exceeded"},
            )
        except RuntimeResultError as error:
            if latest_runtime_result_payload is not None:
                try:
                    await self._record_quota_result(
                        tenant_id,
                        run_id,
                        session,
                        latest_runtime_result_payload,
                    )
                except Exception:
                    # Preserve the provider failure as the primary terminal
                    # reason; quota reconciliation remains independently
                    # recoverable from durable result events.
                    pass
                latest_runtime_result_payload = None
            workspace_durable = await self._recover_failed_workspace(
                tenant_id=tenant_id,
                run=run,
                sandbox=active_sandbox,
                handle=handle,
                baseline=output_baseline,
                final_response=final_response_text,
                message_id=final_response_message_id,
                archive_on_complete=workspace_policy.archive_on_complete,
            )
            latest = await self._runs.get(tenant_id, run_id)
            if latest.status is RunStatus.CANCELLING:
                return await self._move(latest, RunStatus.CANCELLED)
            if latest.status.is_terminal:
                return latest
            payload: dict[str, Any] = {"subtype": error.subtype}
            if error.api_error_status is not None:
                payload["api_error_status"] = error.api_error_status
            if error.user_message is not None:
                payload["message"] = error.user_message
            return await self._move(
                latest,
                RunStatus.FAILED,
                error_code=error.error_code,
                payload=payload,
            )
        except Exception as error:  # noqa: BLE001 - boundary converts failures to Run state
            workspace_durable = await self._recover_failed_workspace(
                tenant_id=tenant_id,
                run=run,
                sandbox=active_sandbox,
                handle=handle,
                baseline=output_baseline,
                final_response=final_response_text,
                message_id=final_response_message_id,
                archive_on_complete=workspace_policy.archive_on_complete,
            )
            return await self._handle_unexpected_error(tenant_id, run_id, error)
        finally:
            if self._credential_revoker is not None:
                await self._credential_revoker(tenant_id, run_id)
            if handle is not None:
                if not workspace_durable and handle.provider == "daytona":
                    handle = handle.model_copy(update={"preserve_remote_workspace": True})
                    try:
                        await self._events.append(
                            tenant_id=tenant_id,
                            run_id=run_id,
                            session_id=run.session_id,
                            event_type="workspace.recovery_retained",
                            payload={
                                "retention_seconds": 3600,
                                "reason": "durable_workspace_commit_failed",
                            },
                        )
                    except Exception:  # noqa: BLE001 - cleanup must still run
                        pass
                with self._stage("harness.sandbox.destroy", {"run.id": run_id}):
                    await active_sandbox.destroy(handle)
