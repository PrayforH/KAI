import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.adapters.memory import (
    InMemoryArtifactRepository,
    InMemoryArtifactStore,
    InMemoryCancellationWakeup,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryTaskQueue,
    InMemoryWorkspaceSnapshotRepository,
)
from harness.application.artifacts import ArtifactService
from harness.application.events import EventService
from harness.application.runs import RunService
from harness.application.workspaces import WorkspaceService
from harness.config import Settings
from harness.context.checkpoint import ContextCheckpointService, TranscriptCheckpoint
from harness.context.models import (
    ContextDigestCreator,
    ContextDigestEntry,
    ContextDigestSource,
)
from harness.context.repositories import InMemoryContextRepository
from harness.context.service import ContextService
from harness.core.events import RunEvent
from harness.core.models import Run, RunStatus, Session
from harness.core.ports import StoredObject
from harness.observability.provider import Observability, build_observability
from harness.observability.redaction import correlation_hash
from harness.policy.models import ContextTrust, PolicyDecision, PolicyRule, ToolResultPolicyRule
from harness.policy.profiles import default_policy_profiles
from harness.policy.results import ResultPolicyEngine
from harness.policy.rules import PolicyEngine, default_policy_rules
from harness.policy.runtime import ResolvedPolicy
from harness.quota.models import QuotaResource, ReplaceQuotaPolicyRequest
from harness.quota.repositories import InMemoryQuotaRepository
from harness.quota.service import QuotaService
from harness.reliability.metrics import ReliabilityMetrics
from harness.runtime.base import (
    RuntimeContext,
    RuntimeEvent,
    RuntimeExecutionTimeoutError,
    RuntimeResultError,
)
from harness.runtime.fake import FakeRuntime
from harness.runtime.subagent_governance import SubagentGovernanceError
from harness.runtime.tools import ToolResolutionError
from harness.sandbox.base import SandboxHandle, SandboxIsolation, SandboxProvider
from harness.sandbox.local import LocalSandboxProvider
from harness.worker.orchestrator import (
    PolicyResolver,
    RunOrchestrator,
    RuntimeAssetStager,
    final_artifact_paths,
    read_runtime_artifact,
    terminal_runtime_result,
)

NOW = datetime(2026, 7, 11, tzinfo=UTC)


class CountingEventRepository(InMemoryEventRepository):
    def __init__(self) -> None:
        super().__init__()
        self.list_after_calls = 0

    async def list_after(self, tenant_id: str, run_id: str, after_sequence: int) -> list[RunEvent]:
        self.list_after_calls += 1
        return await super().list_after(tenant_id, run_id, after_sequence)


class FailingCancellationWakeup:
    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_fencing_token: int,
        *,
        timeout_seconds: float,
    ) -> bool:
        raise ConnectionError("redis unavailable")

    async def publish(self, tenant_id: str, run_id: str, fencing_token: int) -> None:
        raise ConnectionError("redis unavailable")


class BlockingPutArtifactStore(InMemoryArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.put_started = asyncio.Event()

    async def put(self, tenant_id: str, artifact_id: str, content: bytes) -> StoredObject:
        self.put_started.set()
        await asyncio.Event().wait()
        raise AssertionError("blocking test store unexpectedly resumed")


class StubTranscriptCheckpoints:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def checkpoint(
        self,
        tenant_id: str,
        project_id: str,
        sdk_session_id: str,
    ) -> TranscriptCheckpoint | None:
        del tenant_id, project_id, sdk_session_id
        if self._fail:
            raise ConnectionError("checkpoint unavailable")
        return TranscriptCheckpoint(
            sdk_session_id_hash=f"sha256:{'a' * 64}",
            transcript_checkpoint_hash=f"sha256:{'b' * 64}",
            entry_count=2,
        )


def test_runtime_artifact_reader_is_workspace_scoped_and_bounded(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "valid.txt").write_bytes(b"valid")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    (workspace / "link.txt").symlink_to(outside)

    path, content = read_runtime_artifact(workspace, "valid.txt", max_bytes=5)

    assert path == workspace / "valid.txt"
    assert content == b"valid"
    with pytest.raises(ValueError, match="escaped"):
        read_runtime_artifact(workspace, "../outside.txt", max_bytes=100)
    with pytest.raises(ValueError, match="escaped|regular file"):
        read_runtime_artifact(workspace, "link.txt", max_bytes=100)
    with pytest.raises(ValueError, match="size limit"):
        read_runtime_artifact(workspace, "valid.txt", max_bytes=4)


def test_final_artifact_paths_accepts_declared_workspace_files_only(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/final.json").write_text("{}")
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/source.json").write_text("{}")

    paths = final_artifact_paths(
        tmp_path,
        "结果：`reports/final.json`；临时：`/tmp/result.json`；输入：`inputs/source.json`",
    )

    assert paths == ("reports/final.json",)


def ids() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return generate


class ToolRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(
            type="tool.request",
            payload={
                "tool_call_id": "task-1",
                "name": "Task",
                "arguments": {"subagent_type": "helper"},
            },
        )
        yield RuntimeEvent(
            type="tool.result",
            payload={"tool_call_id": "task-1", "content": "done", "is_error": False},
        )
        yield RuntimeEvent(type="message.completed")


class TimedOutRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        raise RuntimeExecutionTimeoutError("test runtime timeout")
        yield


class ErrorResultRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        yield RuntimeEvent(
            type="runtime.result",
            payload={"is_error": True, "subtype": "error_max_budget_usd"},
        )
        raise RuntimeResultError("error_max_budget_usd", api_error_status=429)


class MultipleResultRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        yield RuntimeEvent(
            type="runtime.result",
            payload={"usage": {"input_tokens": 6, "output_tokens": 4}},
        )
        yield RuntimeEvent(
            type="runtime.result",
            payload={"usage": {"input_tokens": 9, "output_tokens": 6}},
        )


class HangingAfterEndTurnRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.closed = asyncio.Event()

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        try:
            yield RuntimeEvent(type="message.start")
            yield RuntimeEvent(type="message.delta", payload={"text": "done"})
            yield RuntimeEvent(type="message.completed")
            yield RuntimeEvent(
                type="runtime.result",
                payload={
                    "subtype": "success",
                    "is_error": False,
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 6, "output_tokens": 4},
                },
            )
            await asyncio.Event().wait()
        finally:
            self.closed.set()


class ContentRejectedRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        yield RuntimeEvent(
            type="runtime.result",
            payload={"is_error": True, "subtype": "api_error_400"},
        )
        raise RuntimeResultError(
            "api_error_400",
            api_error_status=400,
            error_code="provider_content_rejected",
            user_message="模型服务拒绝了本轮上下文，请重新运行。",
        )


class CapturingRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[RuntimeContext] = []

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        self.contexts.append(context)
        async for event in super().execute(context):
            yield event


class SessionAwareRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[RuntimeContext] = []

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        self.contexts.append(context)
        sdk_session_id = context.session.claude_session_id or "sdk-session-1"
        yield RuntimeEvent(
            type="runtime.system",
            payload={"subtype": "init", "session_id": sdk_session_id},
        )
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(type="message.delta", payload={"text": "ok"})
        yield RuntimeEvent(type="message.completed")
        yield RuntimeEvent(
            type="runtime.result",
            payload={"subtype": "success", "session_id": sdk_session_id},
        )


class SessionRecoveryRuntime(SessionAwareRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        self.contexts.append(context)
        if len(self.contexts) == 2:
            yield RuntimeEvent(
                type="runtime.session.recovered",
                payload={"previous_session_id": "sdk-session-1"},
            )
            sdk_session_id = "sdk-session-2"
        else:
            sdk_session_id = context.session.claude_session_id or "sdk-session-1"
        yield RuntimeEvent(
            type="runtime.system",
            payload={"subtype": "init", "session_id": sdk_session_id},
        )
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(type="message.delta", payload={"text": "ok"})
        yield RuntimeEvent(type="message.completed")
        yield RuntimeEvent(
            type="runtime.result",
            payload={"subtype": "success", "session_id": sdk_session_id},
        )


class ThreadInvalidatingRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[RuntimeContext] = []

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        self.contexts.append(context)
        if len(self.contexts) == 1:
            yield RuntimeEvent(
                type="runtime.thread.started",
                payload={"thread_id": "sdk-poisoned", "runtime": "claude-agent-sdk"},
            )
            yield RuntimeEvent(
                type="runtime.thread.invalidated",
                payload={
                    "thread_id": "sdk-poisoned",
                    "runtime": "claude-agent-sdk",
                    "reason_code": "turn_failed",
                },
            )
            raise RuntimeResultError("turn_failed")
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(type="message.delta", payload={"text": "fresh thread"})
        yield RuntimeEvent(type="message.completed")


class FencingRefreshRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.runs: InMemoryRunRepository | None = None

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        assert self.runs is not None
        current = await self.runs.get(context.run.tenant_id, context.run.run_id)
        refreshed = current.model_copy(update={"fencing_token": current.fencing_token + 1})
        assert await self.runs.compare_and_set(current.status, refreshed)
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(type="message.delta", payload={"text": "approved"})
        yield RuntimeEvent(type="message.completed")


class WorkspaceOutputRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        output = context.workspace / "outputs" / "report.md"
        output.parent.mkdir(parents=True)
        output.write_text("verified output")
        yield RuntimeEvent(type="message.completed")


class WorkspaceOutputThenModelFailureRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        output = context.workspace / "outputs" / "generated.pptx"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"generated presentation")
        yield RuntimeEvent(
            type="runtime.result",
            payload={"is_error": True, "subtype": "api_error_500"},
        )
        raise RuntimeResultError(
            "api_error_500",
            api_error_status=500,
            error_code="provider_error",
            user_message="模型执行失败",
        )


class DeclaredArtifactRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        report = context.workspace / "reports" / "final.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"status":"verified"}')
        internal = context.workspace / "scratch" / "notes.txt"
        internal.parent.mkdir(parents=True)
        internal.write_text("intermediate")
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(
            type="message.delta",
            payload={"text": "最终产物：`reports/final.json`"},
        )
        yield RuntimeEvent(type="message.completed")


class CommentaryToolFinalRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        report = context.workspace / "outputs" / "final.md"
        report.parent.mkdir(parents=True)
        report.write_text("final")
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(type="message.delta", payload={"text": "先检查工作区。"})
        yield RuntimeEvent(type="message.completed")
        yield RuntimeEvent(
            type="tool.request",
            payload={
                "tool_call_id": "tool-after-commentary",
                "name": "Read",
                "arguments": {"file_path": "brief.md"},
            },
        )
        yield RuntimeEvent(
            type="tool.result",
            payload={
                "tool_call_id": "tool-after-commentary",
                "content": "brief",
                "is_error": False,
            },
        )
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(
            type="message.delta",
            payload={"text": "最终产物：`outputs/final.md`"},
        )
        yield RuntimeEvent(type="message.completed")


class ExistingDeclaredArtifactRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        assert (context.workspace / "reports/previous.html").is_file()
        yield RuntimeEvent(type="message.start")
        yield RuntimeEvent(
            type="message.delta",
            payload={"text": "上一轮文件仍是 `reports/previous.html`"},
        )
        yield RuntimeEvent(type="message.completed")


class ToolResolutionFailureRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        raise ToolResolutionError(
            "published MCP tools are no longer available; "
            "recheck and publish the Agent: mcp__knowledge__search"
        )
        yield


class SubagentGovernanceFailureRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        raise SubagentGovernanceError("subagent event references an undeclared role alias")
        yield


class DomainConflictFailureRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        from harness.core.errors import ConflictError

        raise ConflictError("quota reservation is not active")
        yield


class ContainerSandboxProvider(LocalSandboxProvider):
    async def provision(self, run: Run) -> SandboxHandle:
        handle = await super().provision(run)
        return handle.model_copy(
            update={
                "provider": "daytona",
                "isolation_level": SandboxIsolation.CONTAINER,
            }
        )


class AssetCheckingSandboxProvider(LocalSandboxProvider):
    def __init__(self, root: Path) -> None:
        super().__init__(root=root)
        self.asset_was_ready = False

    async def prepare(self, handle: SandboxHandle) -> None:
        self.asset_was_ready = (handle.path / ".claude/skills/domain-core/SKILL.md").is_file()
        await super().prepare(handle)


class PreloadedDeliverableSandboxProvider(LocalSandboxProvider):
    async def prepare(self, handle: SandboxHandle) -> None:
        previous = handle.path / "reports/previous.html"
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_text("previous turn")
        await super().prepare(handle)


class PausablePrepareSandboxProvider(LocalSandboxProvider):
    def __init__(self, root: Path) -> None:
        super().__init__(root=root)
        self.started = asyncio.Event()
        self.cancelled = False

    async def prepare(self, handle: SandboxHandle) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def arrange(
    tmp_path: Path,
    *,
    fail_runtime: bool = False,
    runtime_override: FakeRuntime | None = None,
    policy: PolicyEngine | None = None,
    observability: Observability | None = None,
    sandbox_override: SandboxProvider | None = None,
    runtime_asset_stager: RuntimeAssetStager | None = None,
    policy_resolver: PolicyResolver | None = None,
    enable_artifacts: bool = False,
    credential_revoker: Callable[[str, str], Awaitable[None]] | None = None,
    quotas: QuotaService | None = None,
    workspaces: WorkspaceService | None = None,
    clock: Callable[[], datetime] = lambda: NOW,
    metrics: ReliabilityMetrics | None = None,
    events_override: InMemoryEventRepository | None = None,
    context_checkpoints: ContextCheckpointService | None = None,
    context_service: ContextService | None = None,
):
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    event_repository = events_override or InMemoryEventRepository()
    runtime = runtime_override or FakeRuntime(fail=fail_runtime)
    sandbox = sandbox_override or LocalSandboxProvider(root=tmp_path)
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.QUEUED,
        idempotency_key="idem-1",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "hello harness"},
    )
    await sessions.add(session)
    await runs.add(run)
    artifact_service = (
        ArtifactService(
            runs=runs,
            repository=InMemoryArtifactRepository(),
            store=InMemoryArtifactStore(),
            id_generator=ids(),
        )
        if enable_artifacts
        else None
    )
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=EventService(
            event_repository,
            InMemoryEventBus(),
            clock=clock,
            id_generator=ids(),
        ),
        runtime=runtime,
        sandbox=sandbox,
        clock=clock,
        policy=policy,
        observability=observability,
        runtime_asset_stager=runtime_asset_stager,
        policy_resolver=policy_resolver,
        artifacts=artifact_service,
        credential_revoker=credential_revoker,
        quotas=quotas,
        workspaces=workspaces,
        metrics=metrics,
        context_checkpoints=context_checkpoints,
        context_service=context_service,
    )
    return orchestrator, runtime, runs, event_repository


def context_checkpoints(*, fail: bool = False) -> ContextCheckpointService:
    contexts = ContextService(
        InMemoryContextRepository(),
        clock=lambda: NOW,
        id_generator=ids(),
    )
    return ContextCheckpointService(
        contexts,
        StubTranscriptCheckpoints(fail=fail),
    )


@pytest.mark.asyncio
async def test_successful_run_publishes_context_digest_before_terminal(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=SessionAwareRuntime(),
        context_checkpoints=context_checkpoints(),
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    event_types = [event.type for event in emitted]
    assert "context.digest.created" in event_types
    assert event_types.index("context.digest.created") < event_types.index("run.succeeded")
    created = next(event for event in emitted if event.type == "context.digest.created")
    assert created.payload["version"] == 1
    assert created.payload["trust_high_watermark"] == "safe"


@pytest.mark.asyncio
async def test_context_checkpoint_failure_is_observable_and_does_not_fail_answer(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=SessionAwareRuntime(),
        context_checkpoints=context_checkpoints(fail=True),
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    failed = next(event for event in emitted if event.type == "context.digest.failed")
    assert failed.payload == {
        "error_code": "context_checkpoint_failed",
        "error_type": "ConnectionError",
    }


@pytest.mark.asyncio
async def test_fresh_rebased_session_loads_digest_projection_before_runtime(
    tmp_path: Path,
) -> None:
    contexts = ContextService(
        InMemoryContextRepository(),
        clock=lambda: NOW,
        id_generator=ids(),
    )
    await contexts.create_digest(
        tenant_id="tenant-a",
        owner_user_id="user-1",
        session_id="session-1",
        source=ContextDigestSource(
            sdk_session_id_hash=f"sha256:{'a' * 64}",
            through_run_id="run-before-rebase",
            through_event_sequence=12,
            transcript_checkpoint_hash=f"sha256:{'b' * 64}",
        ),
        created_by=ContextDigestCreator(
            route_id="context-rebase-v1",
            model="deterministic",
            prompt_revision="context-rebase-v1",
        ),
        facts=(
            ContextDigestEntry(
                text="The release target is P1",
                source_refs=("run:run-before-rebase:event:12",),
                trust=ContextTrust.SAFE,
            ),
        ),
    )
    runtime = CapturingRuntime()
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=runtime,
        context_service=contexts,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert "The release target is P1" in runtime.contexts[0].context_projection
    emitted = await events.list_after("tenant-a", "run-1", 0)
    loaded = next(event for event in emitted if event.type == "context.recovery.loaded")
    assert loaded.payload == {"mode": "durable_digest"}


@pytest.mark.asyncio
async def test_worker_admission_rejects_unadmitted_run_before_sandbox_and_converges_failed(
    tmp_path: Path,
) -> None:
    quotas = QuotaService(InMemoryQuotaRepository())
    await quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner-a",
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            limits={QuotaResource.CONCURRENT_RUNS: 1},
        ),
    )
    await quotas.reserve(
        tenant_id="tenant-a",
        resource=QuotaResource.CONCURRENT_RUNS,
        amount=1,
        subject_id="another-run",
        idempotency_key="another-run:concurrency",
    )
    orchestrator, _, _, events = await arrange(tmp_path, quotas=quotas)

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    assert result.error_code == "quota_exceeded_concurrent_runs"
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert [event.type for event in emitted] == ["run.provisioning", "run.failed"]


@pytest.mark.asyncio
async def test_multiple_cumulative_runtime_results_settle_quota_once(
    tmp_path: Path,
) -> None:
    repository = InMemoryQuotaRepository()
    quotas = QuotaService(repository)
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=MultipleResultRuntime(),
        quotas=quotas,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert [event.type for event in emitted].count("runtime.result") == 2
    reservation = await repository.get_reservation(
        "tenant-a",
        "run:run-1:actual-tokens",
    )
    assert reservation is not None
    assert reservation.amount == 15


def test_only_successful_end_turn_result_is_a_terminal_stream_boundary() -> None:
    assert terminal_runtime_result(
        RuntimeEvent(
            type="runtime.result",
            payload={
                "subtype": "success",
                "is_error": False,
                "stop_reason": "end_turn",
            },
        )
    )
    assert not terminal_runtime_result(
        RuntimeEvent(
            type="runtime.result",
            payload={"subtype": "success", "usage": {"input_tokens": 1}},
        )
    )
    assert not terminal_runtime_result(
        RuntimeEvent(
            type="runtime.result",
            payload={
                "subtype": "api_error_500",
                "is_error": True,
                "stop_reason": "end_turn",
            },
        )
    )


@pytest.mark.asyncio
async def test_successful_end_turn_completes_when_provider_stream_stays_open(
    tmp_path: Path,
) -> None:
    runtime = HangingAfterEndTurnRuntime()
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=runtime,
    )

    result = await asyncio.wait_for(orchestrator.execute("tenant-a", "run-1"), timeout=1)

    assert result.status is RunStatus.SUCCEEDED
    assert runtime.closed.is_set()
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-2].type == "runtime.result"
    assert emitted[-1].type == "run.succeeded"


@pytest.mark.asyncio
async def test_run_completion_revokes_every_run_scoped_credential(
    tmp_path: Path,
) -> None:
    revoked: list[tuple[str, str]] = []

    async def revoke(tenant_id: str, run_id: str) -> None:
        revoked.append((tenant_id, run_id))

    orchestrator, _, _, _ = await arrange(
        tmp_path,
        credential_revoker=revoke,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert revoked == [("tenant-a", "run-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize("container_isolation", [False, True])
async def test_workspace_outputs_are_published_as_artifacts_for_every_sandbox(
    tmp_path: Path,
    container_isolation: bool,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=WorkspaceOutputRuntime(),
        sandbox_override=(
            ContainerSandboxProvider(root=tmp_path)
            if container_isolation
            else LocalSandboxProvider(root=tmp_path)
        ),
        enable_artifacts=True,
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)
    artifact_events = [event for event in recorded if event.type == "artifact.ready"]

    assert completed.status is RunStatus.SUCCEEDED
    assert len(artifact_events) == 1
    assert artifact_events[0].payload["name"] == "report.md"
    assert artifact_events[0].payload["source"] == "workspace-output"
    assert artifact_events[0].payload["source_path"] == "outputs/report.md"


@pytest.mark.asyncio
async def test_workspace_archive_failure_after_model_success_is_non_terminal(
    tmp_path: Path,
) -> None:
    workspaces = WorkspaceService(
        InMemoryArtifactStore(),
        snapshots=InMemoryWorkspaceSnapshotRepository(),
        max_archive_members=1,
    )
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=WorkspaceOutputRuntime(),
        enable_artifacts=True,
        workspaces=workspaces,
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)

    assert completed.status is RunStatus.SUCCEEDED
    assert any(event.type == "artifact.ready" for event in recorded)
    archive_failure = next(event for event in recorded if event.type == "workspace.archive.failed")
    assert archive_failure.payload == {
        "error_code": "workspace_archive_failed",
        "error_type": "ValueError",
    }
    assert recorded[-1].type == "run.succeeded"


@pytest.mark.asyncio
async def test_workspace_outputs_survive_a_model_failure(tmp_path: Path) -> None:
    artifact_store = InMemoryArtifactStore()
    snapshots = InMemoryWorkspaceSnapshotRepository()
    workspaces = WorkspaceService(artifact_store, snapshots=snapshots)
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=WorkspaceOutputThenModelFailureRuntime(),
        enable_artifacts=True,
        workspaces=workspaces,
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)
    artifact_events = [event for event in recorded if event.type == "artifact.ready"]

    assert completed.status is RunStatus.FAILED
    assert completed.error_code == "provider_error"
    assert len(artifact_events) == 1
    assert artifact_events[0].payload["name"] == "generated.pptx"
    assert artifact_events[0].payload["source"] == "workspace-output"
    assert artifact_events[0].payload["source_path"] == "outputs/generated.pptx"
    assert next(
        index for index, event in enumerate(recorded) if event.type == "artifact.ready"
    ) < next(index for index, event in enumerate(recorded) if event.type == "run.failed")
    archived = next(event for event in recorded if event.type == "workspace.archived")
    assert archived.payload["recovered_from_failure"] is True
    snapshot = await snapshots.latest("tenant-a", "session-1")
    assert snapshot is not None
    restored = tmp_path / "restored"
    await workspaces.restore(snapshot, workspace=restored)
    assert (restored / "outputs/generated.pptx").read_bytes() == b"generated presentation"


@pytest.mark.asyncio
async def test_workspace_outputs_skip_paths_already_published_by_runtime(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=WorkspaceOutputRuntime(),
        enable_artifacts=True,
    )
    await events.append(
        RunEvent(
            event_id="event-runtime-artifact",
            tenant_id="tenant-a",
            run_id="run-1",
            session_id="session-1",
            sequence=1,
            type="artifact.ready",
            timestamp=NOW,
            payload={
                "artifact_id": "artifact-runtime",
                "name": "report.md",
                "source_path": "outputs/report.md",
            },
        )
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)
    artifact_events = [event for event in recorded if event.type == "artifact.ready"]

    assert completed.status is RunStatus.SUCCEEDED
    assert len(artifact_events) == 1
    assert artifact_events[0].payload["artifact_id"] == "artifact-runtime"


@pytest.mark.asyncio
async def test_final_response_declared_file_is_published_outside_outputs(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=DeclaredArtifactRuntime(),
        enable_artifacts=True,
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)
    artifact_events = [event for event in recorded if event.type == "artifact.ready"]

    assert completed.status is RunStatus.SUCCEEDED
    assert [event.payload["name"] for event in artifact_events] == ["reports/final.json"]
    assert artifact_events[0].payload["source"] == "final-response"


@pytest.mark.asyncio
async def test_unchanged_file_from_previous_turn_is_not_republished(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=ExistingDeclaredArtifactRuntime(),
        sandbox_override=PreloadedDeliverableSandboxProvider(root=tmp_path),
        enable_artifacts=True,
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)

    assert completed.status is RunStatus.SUCCEEDED
    assert not [event for event in recorded if event.type == "artifact.ready"]


@pytest.mark.asyncio
async def test_tool_and_artifact_keep_their_assistant_message_ownership(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=CommentaryToolFinalRuntime(),
        enable_artifacts=True,
    )

    completed = await orchestrator.execute("tenant-a", "run-1")
    recorded = await events.list_after("tenant-a", "run-1", 0)
    starts = [event for event in recorded if event.type == "message.start"]
    commentary_message_id = starts[0].payload["message_id"]
    final_message_id = starts[1].payload["message_id"]
    tool_request = next(
        event
        for event in recorded
        if event.type == "tool.request"
        and event.payload.get("tool_call_id") == "tool-after-commentary"
    )
    artifact = next(event for event in recorded if event.type == "artifact.ready")

    assert completed.status is RunStatus.SUCCEEDED
    assert commentary_message_id != final_message_id
    assert tool_request.payload["message_id"] == commentary_message_id
    assert artifact.payload["message_id"] == final_message_id


@pytest.mark.asyncio
async def test_runtime_assets_are_staged_before_sandbox_prepare(tmp_path: Path) -> None:
    sandbox = AssetCheckingSandboxProvider(tmp_path)

    async def stage_assets(
        _tenant_id: str,
        _owner_user_id: str,
        _agent_name: str,
        _agent_version: str,
        workspace: Path,
        _allow_validated_graph: bool,
    ) -> tuple[str, ...]:
        target = workspace / ".claude/skills/domain-core"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("immutable")
        return ("domain-core",)

    orchestrator, _, _, events = await arrange(
        tmp_path,
        sandbox_override=sandbox,
        runtime_asset_stager=stage_assets,
    )

    await orchestrator.execute("tenant-a", "run-1")

    assert sandbox.asset_was_ready is True
    emitted = await events.list_after("tenant-a", "run-1", 0)
    staged = [event for event in emitted if event.type == "agent.assets.staged"]
    assert staged[0].payload == {"skills": ["domain-core"]}


@pytest.mark.asyncio
async def test_manifest_policy_resolver_overrides_generic_worker_policy(
    tmp_path: Path,
) -> None:
    async def resolve_policy(
        _tenant_id: str,
        _owner_user_id: str,
        _agent_name: str,
        _agent_version: str,
    ) -> PolicyEngine:
        return default_policy_profiles().resolve("production-read-only")

    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=ToolRuntime(),
        policy=PolicyEngine(default_policy_rules()),
        policy_resolver=resolve_policy,
    )

    await orchestrator.execute("tenant-a", "run-1")

    emitted = await events.list_after("tenant-a", "run-1", 0)
    denied = [
        event
        for event in emitted
        if event.type == "tool.result"
        and event.payload.get("error", {}).get("code") == "policy_denied"
    ]
    assert denied


@pytest.mark.asyncio
async def test_governed_policy_snapshot_is_recorded_and_enforced(
    tmp_path: Path,
) -> None:
    async def resolve_policy(
        _tenant_id: str,
        _owner_user_id: str,
        _agent_name: str,
        _agent_version: str,
    ) -> ResolvedPolicy:
        return ResolvedPolicy(
            policy_id="governed-production",
            revision=7,
            content_hash="sha256:governed-policy",
            call_policy=PolicyEngine(
                [
                    PolicyRule(
                        name="deny-delegation",
                        tool="Task",
                        decision=PolicyDecision.DENY,
                    )
                ]
            ),
            result_policy=ResultPolicyEngine(
                [
                    ToolResultPolicyRule(
                        name="external-results",
                        tool="mcp__external__*",
                        trust=ContextTrust.UNTRUSTED,
                    )
                ]
            ),
        )

    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=ToolRuntime(),
        policy=PolicyEngine(default_policy_rules()),
        policy_resolver=resolve_policy,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    emitted = await events.list_after("tenant-a", "run-1", 0)
    policy_event = next(event for event in emitted if event.type == "policy.resolved")
    denied = next(
        event
        for event in emitted
        if event.type == "tool.result"
        and event.payload.get("error", {}).get("code") == "policy_denied"
    )
    assert result.status is RunStatus.SUCCEEDED
    assert policy_event.payload == {
        "policy_id": "governed-production",
        "revision": 7,
        "content_hash": "sha256:governed-policy",
    }
    assert policy_event.sequence < denied.sequence
    assert denied.payload["error"]["rule"] == "deny-delegation"


@pytest.mark.asyncio
async def test_executes_run_and_cleans_sandbox(tmp_path: Path) -> None:
    orchestrator, runtime, runs, event_repository = await arrange(tmp_path)

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert runtime.execution_count == 1
    assert list(tmp_path.iterdir()) == []
    events = await event_repository.list_after("tenant-a", "run-1", 0)
    assert [event.type for event in events] == [
        "run.provisioning",
        "run.running",
        "message.start",
        "message.delta",
        "message.completed",
        "run.succeeded",
    ]
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_known_tool_result_does_not_rescan_durable_event_history(
    tmp_path: Path,
) -> None:
    events = CountingEventRepository()
    orchestrator, _, _, _ = await arrange(
        tmp_path,
        runtime_override=ToolRuntime(),
        events_override=events,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert events.list_after_calls == 0


@pytest.mark.asyncio
async def test_records_queue_wait_first_runtime_event_and_first_text(
    tmp_path: Path,
) -> None:
    current = [NOW + timedelta(seconds=2)]

    class StageTimingRuntime(FakeRuntime):
        async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
            del context
            yield RuntimeEvent(type="model.route.selected", payload={"route_id": "fast"})
            current[0] = NOW + timedelta(seconds=3)
            yield RuntimeEvent(type="runtime.system", payload={"subtype": "init"})
            current[0] = NOW + timedelta(seconds=5)
            yield RuntimeEvent(type="message.start")
            yield RuntimeEvent(type="message.delta", payload={"text": "hello"})
            yield RuntimeEvent(type="message.completed")

    metrics = ReliabilityMetrics()
    orchestrator, _, _, _ = await arrange(
        tmp_path,
        runtime_override=StageTimingRuntime(),
        clock=lambda: current[0],
        metrics=metrics,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert metrics.quantile(
        "harness_run_stage_duration_seconds",
        0.95,
        labels={"stage": "queue_wait"},
    ) == (2, 1)
    assert metrics.quantile(
        "harness_run_stage_duration_seconds",
        0.95,
        labels={"stage": "environment_prepare"},
    ) == (0, 1)
    assert metrics.quantile(
        "harness_run_stage_duration_seconds",
        0.95,
        labels={"stage": "runtime_first_event"},
    ) == (0, 1)
    assert metrics.quantile(
        "harness_run_stage_duration_seconds",
        0.95,
        labels={"stage": "provider_first_event"},
    ) == (1, 1)
    assert metrics.quantile(
        "harness_run_stage_duration_seconds",
        0.95,
        labels={"stage": "runtime_first_text"},
    ) == (3, 1)


@pytest.mark.asyncio
async def test_recovered_provisioning_run_does_not_record_queue_wait_again(
    tmp_path: Path,
) -> None:
    metrics = ReliabilityMetrics()
    orchestrator, _, runs, _ = await arrange(tmp_path, metrics=metrics)
    queued = await runs.get("tenant-a", "run-1")
    recovering = queued.model_copy(
        update={
            "status": RunStatus.PROVISIONING,
            "fencing_token": queued.fencing_token + 1,
        }
    )
    assert await runs.compare_and_set(RunStatus.QUEUED, recovering)

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert metrics.quantile(
        "harness_run_stage_duration_seconds",
        0.95,
        labels={"stage": "queue_wait"},
    ) == (None, 0)


@pytest.mark.asyncio
async def test_terminal_transition_refreshes_fencing_after_inline_approval(
    tmp_path: Path,
) -> None:
    runtime = FencingRefreshRuntime()
    orchestrator, _, runs, events = await arrange(tmp_path, runtime_override=runtime)
    runtime.runs = runs

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.SUCCEEDED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-1].type == "run.succeeded"


@pytest.mark.asyncio
async def test_next_run_receives_bound_claude_session_for_resume(
    tmp_path: Path,
) -> None:
    runtime = SessionAwareRuntime()
    orchestrator, _, runs, _ = await arrange(
        tmp_path,
        runtime_override=runtime,
    )

    first = await orchestrator.execute("tenant-a", "run-1")
    second_run = Run(
        run_id="run-2",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.QUEUED,
        idempotency_key="idem-2",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "what did I say?"},
    )
    await runs.add(second_run)
    second = await orchestrator.execute("tenant-a", "run-2")

    assert first.status is RunStatus.SUCCEEDED
    assert second.status is RunStatus.SUCCEEDED
    assert runtime.contexts[0].session.claude_session_id is None
    assert runtime.contexts[1].session.claude_session_id == "sdk-session-1"


@pytest.mark.asyncio
async def test_stale_sdk_session_is_atomically_rebound_after_recovery(
    tmp_path: Path,
) -> None:
    runtime = SessionRecoveryRuntime()
    orchestrator, _, runs, events = await arrange(tmp_path, runtime_override=runtime)

    results = [await orchestrator.execute("tenant-a", "run-1")]
    for sequence in (2, 3):
        run = Run(
            run_id=f"run-{sequence}",
            session_id="session-1",
            tenant_id="tenant-a",
            status=RunStatus.QUEUED,
            idempotency_key=f"idem-{sequence}",
            created_at=NOW,
            updated_at=NOW,
            input={"prompt": f"turn {sequence}"},
        )
        await runs.add(run)
        results.append(await orchestrator.execute("tenant-a", run.run_id))

    assert [result.status for result in results] == [
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
    ]
    assert runtime.contexts[0].session.claude_session_id is None
    assert runtime.contexts[1].session.claude_session_id == "sdk-session-1"
    assert runtime.contexts[2].session.claude_session_id == "sdk-session-2"
    recovered_events = await events.list_after("tenant-a", "run-2", 0)
    assert any(event.type == "runtime.session.recovered" for event in recovered_events)


@pytest.mark.asyncio
async def test_failed_runtime_can_invalidate_thread_before_next_run(tmp_path: Path) -> None:
    runtime = ThreadInvalidatingRuntime()
    orchestrator, _, runs, events = await arrange(tmp_path, runtime_override=runtime)

    first = await orchestrator.execute("tenant-a", "run-1")
    second_run = Run(
        run_id="run-2",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.QUEUED,
        idempotency_key="idem-2",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "retry"},
    )
    await runs.add(second_run)
    second = await orchestrator.execute("tenant-a", "run-2")

    assert first.status is RunStatus.FAILED
    assert second.status is RunStatus.SUCCEEDED
    assert runtime.contexts[1].session.resolved_runtime_thread_id is None
    first_events = await events.list_after("tenant-a", "run-1", 0)
    assert any(event.type == "runtime.thread.invalidated" for event in first_events)


@pytest.mark.asyncio
async def test_runtime_timeout_has_a_distinct_terminal_status(tmp_path: Path) -> None:
    orchestrator, _, runs, events = await arrange(tmp_path, runtime_override=TimedOutRuntime())

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.TIMED_OUT
    assert result.error_code == "runtime_timeout"
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.TIMED_OUT
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-1].type == "run.timed_out"


@pytest.mark.asyncio
async def test_sdk_error_result_cannot_be_recorded_as_success(tmp_path: Path) -> None:
    orchestrator, _, runs, events = await arrange(tmp_path, runtime_override=ErrorResultRuntime())

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    assert result.error_code == "runtime_result_error"
    assert (await runs.get("tenant-a", "run-1")).status is RunStatus.FAILED
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-2].type == "runtime.result"
    assert emitted[-1].type == "run.failed"
    assert emitted[-1].payload == {
        "subtype": "error_max_budget_usd",
        "api_error_status": 429,
        "error_code": "runtime_result_error",
    }


@pytest.mark.asyncio
async def test_provider_content_rejection_keeps_a_safe_recoverable_failure(
    tmp_path: Path,
) -> None:
    orchestrator, _, runs, events = await arrange(
        tmp_path, runtime_override=ContentRejectedRuntime()
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    assert result.error_code == "provider_content_rejected"
    assert (await runs.get("tenant-a", "run-1")).error_code == ("provider_content_rejected")
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert emitted[-1].payload == {
        "subtype": "api_error_400",
        "api_error_status": 400,
        "message": "模型服务拒绝了本轮上下文，请重新运行。",
        "error_code": "provider_content_rejected",
    }


@pytest.mark.asyncio
async def test_passes_provisioned_sandbox_facts_to_runtime(tmp_path: Path) -> None:
    runtime = CapturingRuntime()
    orchestrator, _, _, event_repository = await arrange(
        tmp_path,
        runtime_override=runtime,
        sandbox_override=ContainerSandboxProvider(root=tmp_path),
    )

    await orchestrator.execute("tenant-a", "run-1")

    assert len(runtime.contexts) == 1
    assert runtime.contexts[0].sandbox_provider == "daytona"
    assert runtime.contexts[0].sandbox_isolation is SandboxIsolation.CONTAINER
    assert runtime.contexts[0].assistant_message_id.startswith("assistant-run-1-")
    events = await event_repository.list_after("tenant-a", "run-1", 0)
    started = next(event for event in events if event.type == "message.start")
    assert started.payload["message_id"] == runtime.contexts[0].assistant_message_id


@pytest.mark.asyncio
async def test_local_colima_workspace_exposes_command_executor(
    tmp_path: Path,
) -> None:
    runtime = CapturingRuntime()
    orchestrator, _, _, _ = await arrange(
        tmp_path,
        runtime_override=runtime,
        sandbox_override=LocalSandboxProvider(root=tmp_path),
    )

    await orchestrator.execute("tenant-a", "run-1")

    assert runtime.contexts[0].sandbox_provider == "local"
    assert runtime.contexts[0].sandbox_command_executor is not None


@pytest.mark.asyncio
async def test_executes_run_with_stage_level_traces(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    observability = build_observability(
        Settings(otel_enabled=True, otlp_endpoint="http://unused/v1/traces"),
        exporter=exporter,
        processor_factory=SimpleSpanProcessor,
    )
    orchestrator, _, _, _ = await arrange(tmp_path, observability=observability)

    await orchestrator.execute("tenant-a", "run-1")

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} >= {
        "harness.worker.run",
        "harness.sandbox.provision",
        "harness.memory.load",
        "harness.input.process",
        "harness.sandbox.prepare",
        "harness.runtime.execute",
        "harness.sandbox.collect",
        "harness.sandbox.destroy",
    }
    assert all(
        span.attributes is not None
        and span.attributes["langfuse.session.id"] == "session-1"
        and span.attributes["langfuse.trace.metadata.run_id"] == "run-1"
        for span in spans
        if span.name.startswith("harness.")
    )
    worker_span = next(span for span in spans if span.name == "harness.worker.run")
    assert worker_span.attributes is not None
    assert worker_span.attributes["langfuse.observation.type"] == "agent"
    assert worker_span.attributes["langfuse.trace.name"] == "agent-run"
    assert worker_span.attributes["langfuse.user.id"] == correlation_hash("user-1")
    assert worker_span.attributes["langfuse.trace.metadata.agent_name"] == "echo-agent"
    progress_span = next(span for span in spans if span.name == "assistant-progress")
    assert progress_span.attributes is not None
    assert progress_span.attributes["langfuse.observation.type"] == "event"


@pytest.mark.asyncio
async def test_duplicate_delivery_does_not_execute_twice(tmp_path: Path) -> None:
    orchestrator, runtime, _, _ = await arrange(tmp_path)

    first = await orchestrator.execute("tenant-a", "run-1")
    second = await orchestrator.execute("tenant-a", "run-1")

    assert first == second
    assert runtime.execution_count == 1


@pytest.mark.asyncio
async def test_runtime_failure_marks_run_failed_and_cleans_sandbox(tmp_path: Path) -> None:
    orchestrator, runtime, _, event_repository = await arrange(tmp_path, fail_runtime=True)

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    assert result.error_code == "runtime_error"
    assert runtime.execution_count == 1
    assert list(tmp_path.iterdir()) == []
    events = await event_repository.list_after("tenant-a", "run-1", 0)
    assert events[-1].type == "run.failed"


@pytest.mark.asyncio
async def test_tool_resolution_failure_explains_required_agent_sync(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, event_repository = await arrange(
        tmp_path,
        runtime_override=ToolResolutionFailureRuntime(),
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    events = await event_repository.list_after("tenant-a", "run-1", 0)
    assert events[-1].payload == {
        "error_code": "runtime_error",
        "error_type": "ToolResolutionError",
        "message": (
            "published MCP tools are no longer available; "
            "recheck and publish the Agent: mcp__knowledge__search"
        ),
    }


@pytest.mark.asyncio
async def test_subagent_governance_failure_preserves_actionable_reason(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, event_repository = await arrange(
        tmp_path,
        runtime_override=SubagentGovernanceFailureRuntime(),
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    events = await event_repository.list_after("tenant-a", "run-1", 0)
    assert events[-1].payload == {
        "error_code": "runtime_error",
        "error_type": "SubagentGovernanceError",
        "message": "subagent event references an undeclared role alias",
    }


@pytest.mark.asyncio
async def test_non_ownership_conflict_cannot_leave_run_spinning(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, event_repository = await arrange(
        tmp_path,
        runtime_override=DomainConflictFailureRuntime(),
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    events = await event_repository.list_after("tenant-a", "run-1", 0)
    assert events[-1].payload == {
        "error_code": "runtime_error",
        "error_type": "ConflictError",
        "message": "quota reservation is not active",
    }


class PausableRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.resume = asyncio.Event()

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        self.started.set()
        yield RuntimeEvent(type="message.start")
        await self.resume.wait()
        yield RuntimeEvent(type="message.delta", payload={"text": "too late"})


class BackgroundSubRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        yield RuntimeEvent(
            type="subagent.started",
            payload={
                "event_schema": "harness.subagent.v1",
                "task_id": "background-one",
                "alias": "researcher",
                "agent_name": "helper",
                "agent_version": "1.0.0",
                "policy_profile": "read-only",
                "depth": 1,
            },
        )
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class SlowCancellationCleanupRuntime(BackgroundSubRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_finished = asyncio.Event()

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        try:
            async for event in super().execute(context):
                yield event
        except asyncio.CancelledError:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.cleanup_finished.set()
            raise


class FailureAfterSubRuntime(FakeRuntime):
    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        yield RuntimeEvent(
            type="subagent.started",
            payload={
                "event_schema": "harness.subagent.v1",
                "task_id": "failed-child",
                "alias": "researcher",
                "agent_name": "helper",
                "agent_version": "1.0.0",
                "policy_profile": "read-only",
                "depth": 1,
            },
        )
        raise RuntimeError("injected parent failure")


class ContextBoundRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._marker: ContextVar[str] = ContextVar("runtime_context_marker")
        self.closed_cleanly = False

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        del context
        token = self._marker.set("active")
        try:
            yield RuntimeEvent(type="message.start")
            await asyncio.sleep(0)
            yield RuntimeEvent(type="message.completed")
        finally:
            self._marker.reset(token)
            self.closed_cleanly = True


@pytest.mark.asyncio
async def test_policy_keeps_tool_request_for_ui_before_decision(tmp_path: Path) -> None:
    orchestrator, _, _, event_repository = await arrange(
        tmp_path,
        runtime_override=ToolRuntime(),
        policy=PolicyEngine(default_policy_rules()),
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    events = await event_repository.list_after("tenant-a", "run-1", 0)
    tool_events = [event.type for event in events if event.type.startswith("tool.")]
    request = next(event for event in events if event.type == "tool.request")
    assert result.status is RunStatus.SUCCEEDED
    assert tool_events == ["tool.request", "tool.allowed", "tool.result"]
    assert request.payload["message_id"] == next(
        event.payload["message_id"] for event in events if event.type == "message.start"
    )


@pytest.mark.asyncio
async def test_cancellation_during_runtime_stops_at_next_event_boundary(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    runtime = PausableRuntime()
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.QUEUED,
        idempotency_key="cancel-boundary",
        created_at=NOW,
        updated_at=NOW,
    )
    await sessions.add(session)
    await runs.add(run)
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids()),
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: NOW,
    )

    execution = asyncio.create_task(orchestrator.execute("tenant-a", "run-1"))
    await runtime.started.wait()
    while (await runs.get("tenant-a", "run-1")).status is not RunStatus.RUNNING:
        await asyncio.sleep(0)
    current = await runs.get("tenant-a", "run-1")
    cancelling = current.model_copy(
        update={
            "status": RunStatus.CANCELLING,
            "fencing_token": current.fencing_token + 1,
        }
    )
    assert await runs.compare_and_set(RunStatus.RUNNING, cancelling)
    runtime.resume.set()

    result = await execution
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert result.status is RunStatus.CANCELLED
    assert "message.delta" not in [item.type for item in emitted]


@pytest.mark.asyncio
async def test_cancellation_interrupts_background_sub_and_emits_child_terminal(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    runtime = BackgroundSubRuntime()
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.QUEUED,
        idempotency_key="cancel-background",
        created_at=NOW,
        updated_at=NOW,
    )
    await sessions.add(session)
    await runs.add(run)
    metrics = ReliabilityMetrics()
    cancellation_wakeup = InMemoryCancellationWakeup()
    workspace_store = BlockingPutArtifactStore()
    event_service = EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids())
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=event_service,
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: NOW,
        cancellation_poll_interval_seconds=1.0,
        cancellation_wakeup=cancellation_wakeup,
        metrics=metrics,
        workspaces=WorkspaceService(
            workspace_store,
            snapshots=InMemoryWorkspaceSnapshotRepository(),
        ),
    )
    run_service = RunService(
        sessions,
        runs,
        InMemoryTaskQueue(),
        event_service,
        clock=lambda: NOW,
        id_generator=ids(),
        metrics=metrics,
        cancellation_wakeup=cancellation_wakeup,
    )

    execution = asyncio.create_task(orchestrator.execute("tenant-a", "run-1"))
    await runtime.started.wait()
    cancelling = await run_service.cancel("tenant-a", "run-1")
    assert cancelling.status is RunStatus.CANCELLING

    result = await asyncio.wait_for(execution, timeout=0.1)
    emitted = await events.list_after("tenant-a", "run-1", 0)
    child_terminal = next(event for event in emitted if event.type == "subagent.failed")

    assert result.status is RunStatus.CANCELLED
    assert runtime.cancelled is True
    assert workspace_store.put_started.is_set() is False
    assert child_terminal.payload["task_id"] == "background-one"
    assert child_terminal.payload["error_code"] == "parent_cancelled"
    assert emitted[-1].type == "run.cancelled"
    convergence, count = metrics.quantile(
        "harness_workflow_convergence_seconds",
        0.95,
        labels={"workflow": "run.cancel"},
    )
    assert convergence == 0
    assert count == 1


@pytest.mark.asyncio
async def test_cancellation_does_not_wait_for_slow_runtime_cleanup(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    runtime = SlowCancellationCleanupRuntime()
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.QUEUED,
        idempotency_key="cancel-slow-runtime-cleanup",
        created_at=NOW,
        updated_at=NOW,
    )
    await sessions.add(session)
    await runs.add(run)
    cancellation_wakeup = InMemoryCancellationWakeup()
    event_service = EventService(events, InMemoryEventBus(), clock=lambda: NOW, id_generator=ids())
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=event_service,
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: NOW,
        cancellation_poll_interval_seconds=1.0,
        cancellation_wakeup=cancellation_wakeup,
    )
    run_service = RunService(
        sessions,
        runs,
        InMemoryTaskQueue(),
        event_service,
        clock=lambda: NOW,
        id_generator=ids(),
        cancellation_wakeup=cancellation_wakeup,
    )

    execution = asyncio.create_task(orchestrator.execute("tenant-a", "run-1"))
    await runtime.started.wait()
    await run_service.cancel("tenant-a", "run-1")

    result = await asyncio.wait_for(execution, timeout=0.1)
    assert result.status is RunStatus.CANCELLED
    assert runtime.cleanup_started.is_set()
    assert runtime.cleanup_finished.is_set() is False
    assert [event.type for event in await events.list_after("tenant-a", "run-1", 0)][
        -1
    ] == "run.cancelled"

    runtime.cleanup_release.set()
    await asyncio.wait_for(runtime.cleanup_finished.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_parent_failure_emits_terminal_for_every_started_subagent(
    tmp_path: Path,
) -> None:
    orchestrator, _, _, events = await arrange(
        tmp_path,
        runtime_override=FailureAfterSubRuntime(),
    )

    result = await orchestrator.execute("tenant-a", "run-1")
    emitted = await events.list_after("tenant-a", "run-1", 0)
    child_terminal = next(event for event in emitted if event.type == "subagent.failed")

    assert result.status is RunStatus.FAILED
    assert child_terminal.payload["task_id"] == "failed-child"
    assert child_terminal.payload["error_code"] == "parent_failed"
    assert emitted.index(child_terminal) < len(emitted) - 1
    assert emitted[-1].type == "run.failed"


@pytest.mark.asyncio
async def test_cancellation_polling_keeps_runtime_context_on_one_producer_task(
    tmp_path: Path,
) -> None:
    runtime = ContextBoundRuntime()
    orchestrator, _, _, _ = await arrange(tmp_path, runtime_override=runtime)

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert runtime.closed_cleanly is True


@pytest.mark.asyncio
async def test_cancellation_interrupts_sandbox_prepare_when_wakeup_is_unavailable(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    sandbox = PausablePrepareSandboxProvider(tmp_path)
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.QUEUED,
        idempotency_key="cancel-prepare",
        created_at=NOW,
        updated_at=NOW,
    )
    await sessions.add(session)
    await runs.add(run)
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=EventService(
            events,
            InMemoryEventBus(),
            clock=lambda: NOW,
            id_generator=ids(),
        ),
        runtime=FakeRuntime(),
        sandbox=sandbox,
        clock=lambda: NOW,
        cancellation_poll_interval_seconds=0.01,
        cancellation_wakeup=FailingCancellationWakeup(),
    )

    execution = asyncio.create_task(orchestrator.execute("tenant-a", "run-1"))
    await sandbox.started.wait()
    current = await runs.get("tenant-a", "run-1")
    assert current.status is RunStatus.PROVISIONING
    cancelling = current.model_copy(
        update={
            "status": RunStatus.CANCELLING,
            "fencing_token": current.fencing_token + 1,
        }
    )
    assert await runs.compare_and_set(RunStatus.PROVISIONING, cancelling)

    result = await asyncio.wait_for(execution, timeout=1)
    emitted = await events.list_after("tenant-a", "run-1", 0)
    assert result.status is RunStatus.CANCELLED
    assert sandbox.cancelled is True
    assert [item.type for item in emitted][-1] == "run.cancelled"


@pytest.mark.asyncio
async def test_recovered_provisioning_run_is_reclaimed_and_completed(
    tmp_path: Path,
) -> None:
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.PROVISIONING,
        fencing_token=1,
        idempotency_key="recover-provisioning",
        created_at=NOW,
        updated_at=NOW,
    )
    await sessions.add(session)
    await runs.add(run)
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=EventService(
            events,
            InMemoryEventBus(),
            clock=lambda: NOW,
            id_generator=ids(),
        ),
        runtime=FakeRuntime(),
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: NOW,
    )

    result = await orchestrator.execute("tenant-a", "run-1")
    emitted = await events.list_after("tenant-a", "run-1", 0)

    assert result.status is RunStatus.SUCCEEDED
    assert result.fencing_token == 4
    assert [item.type for item in emitted] == [
        "run.recovered",
        "run.running",
        "message.start",
        "message.delta",
        "message.completed",
        "run.succeeded",
    ]
