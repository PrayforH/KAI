"""Target-environment live Preflight orchestration for Studio Preview."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from contextlib import nullcontext, suppress
from datetime import UTC, datetime

from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifest
from harness.core.models import ExecutionIdentity, Run, RunStatus
from harness.observability.provider import Observability
from harness.policy.models import PolicyContext, PolicyDecision
from harness.policy.profiles import PolicyProfileRegistry
from harness.policy.runtime import ResolvedPolicy
from harness.sandbox.base import SandboxHandle, SandboxProvider
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import CompiledAgentDraft, DraftCompilationError
from harness.studio.models import AgentDraft
from harness.studio.preflight_models import (
    PreflightArtifactProof,
    PreflightCheck,
    PreflightCheckStatus,
    PreflightEvent,
    PreflightResult,
    PreflightResultStatus,
    PreflightStage,
)
from harness.studio.preflight_probes import (
    McpPreflightProbe,
    ModelPreflightProbe,
    PreflightCheckError,
    PreflightEvidence,
)
from harness.studio.preview_controller import PreviewProvisioningError
from harness.studio.preview_models import PreviewDeployment, PreviewStatus
from harness.studio.preview_repositories import PreviewRepository
from harness.studio.service import AgentStudioService

CancelCheck = Callable[[], Awaitable[bool]]
PolicyRuntimeResolver = Callable[[str, str], Awaitable[ResolvedPolicy]]


class _PreflightCancelledError(Exception):
    pass


class LivePreflightRunner:
    """Run every check against the selected target adapters and always clean up."""

    def __init__(
        self,
        *,
        studio: AgentStudioService,
        sandbox: SandboxProvider,
        model_probe: ModelPreflightProbe,
        mcp_probe: McpPreflightProbe,
        policies: PolicyProfileRegistry,
        policy_resolver: PolicyRuntimeResolver | None = None,
        observability: Observability | None = None,
        timeout_seconds: float = 180,
        clock: Callable[[], datetime] | None = None,
        enforce_execution_profile_provider: bool = False,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Preflight timeout must be positive")
        self._studio = studio
        self._sandbox = sandbox
        self._model_probe = model_probe
        self._mcp_probe = mcp_probe
        self._policies = policies
        self._policy_resolver = policy_resolver
        self._observability = observability
        self._timeout_seconds = timeout_seconds
        self._enforce_execution_profile_provider = enforce_execution_profile_provider
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self, preview: PreviewDeployment, *, cancelled: CancelCheck) -> PreflightResult:
        started_at = self._clock()
        checks: list[PreflightCheck] = []
        events: list[PreflightEvent] = []
        draft: AgentDraft | None = None
        compiled: CompiledAgentDraft | None = None
        manifest: AgentManifest | None = None
        handle: SandboxHandle | None = None
        artifact: PreflightArtifactProof | None = None
        result_status = PreflightResultStatus.PASSED
        error_code: str | None = None

        async def record(
            stage: PreflightStage,
            action: Callable[[], Awaitable[PreflightEvidence]],
            *,
            honor_cancel: bool = True,
        ) -> None:
            if honor_cancel and await cancelled():
                raise _PreflightCancelledError
            check_started = self._clock()
            events.append(
                PreflightEvent(
                    sequence=len(events) + 1,
                    eventType="check.started",
                    stage=stage,
                    occurredAt=check_started,
                )
            )
            span = (
                self._observability.span(
                    "harness.studio.preflight.check",
                    attributes={
                        "harness.preview.id": preview.preview_id,
                        "harness.preflight.stage": stage.value,
                    },
                )
                if self._observability is not None
                else nullcontext()
            )
            action_task: asyncio.Future[PreflightEvidence] | None = None
            try:
                with span:
                    if not honor_cancel:
                        evidence = await action()
                    else:
                        action_task = asyncio.ensure_future(action())
                        while not action_task.done():
                            await asyncio.wait({action_task}, timeout=0.25)
                            if not action_task.done() and await cancelled():
                                action_task.cancel()
                                with suppress(asyncio.CancelledError):
                                    await action_task
                                raise _PreflightCancelledError
                        evidence = await action_task
            except asyncio.CancelledError:
                if action_task is not None and not action_task.done():
                    action_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await action_task
                raise
            except _PreflightCancelledError:
                completed = self._clock()
                checks.append(
                    PreflightCheck(
                        stage=stage,
                        status=PreflightCheckStatus.CANCELLED,
                        startedAt=check_started,
                        completedAt=completed,
                        durationMs=_duration_ms(check_started, completed),
                        summary="Preflight cancelled by user",
                        errorCode="preflight_cancelled",
                    )
                )
                events.append(
                    PreflightEvent(
                        sequence=len(events) + 1,
                        eventType="check.completed",
                        stage=stage,
                        occurredAt=completed,
                        status=PreflightCheckStatus.CANCELLED,
                        errorCode="preflight_cancelled",
                    )
                )
                raise
            except PreflightCheckError as failure:
                completed = self._clock()
                check = PreflightCheck(
                    stage=stage,
                    status=PreflightCheckStatus.FAILED,
                    startedAt=check_started,
                    completedAt=completed,
                    durationMs=_duration_ms(check_started, completed),
                    summary=failure.summary,
                    errorCode=failure.error_code,
                )
                checks.append(check)
                events.append(
                    PreflightEvent(
                        sequence=len(events) + 1,
                        eventType="check.completed",
                        stage=stage,
                        occurredAt=completed,
                        status=check.status,
                        errorCode=failure.error_code,
                    )
                )
                raise
            except Exception as failure:
                stable = PreflightCheckError(
                    f"{stage.value}_failed", f"{stage.value.replace('_', ' ').title()} failed"
                )
                completed = self._clock()
                check = PreflightCheck(
                    stage=stage,
                    status=PreflightCheckStatus.FAILED,
                    startedAt=check_started,
                    completedAt=completed,
                    durationMs=_duration_ms(check_started, completed),
                    summary=stable.summary,
                    errorCode=stable.error_code,
                )
                checks.append(check)
                events.append(
                    PreflightEvent(
                        sequence=len(events) + 1,
                        eventType="check.completed",
                        stage=stage,
                        occurredAt=completed,
                        status=check.status,
                        errorCode=stable.error_code,
                    )
                )
                raise stable from failure
            completed = self._clock()
            status = (
                PreflightCheckStatus.SKIPPED if evidence.skipped else PreflightCheckStatus.PASSED
            )
            check = PreflightCheck(
                stage=stage,
                status=status,
                startedAt=check_started,
                completedAt=completed,
                durationMs=_duration_ms(check_started, completed),
                summary=evidence.summary,
                details=dict(evidence.details),
            )
            checks.append(check)
            events.append(
                PreflightEvent(
                    sequence=len(events) + 1,
                    eventType="check.completed",
                    stage=stage,
                    occurredAt=completed,
                    status=status,
                )
            )

        async def bundle_check() -> PreflightEvidence:
            nonlocal draft, compiled, manifest
            draft = await self._studio.get(
                preview.tenant_id, preview.requested_by, preview.draft_id
            )
            if draft.revision != preview.draft_revision:
                raise PreflightCheckError(
                    "preflight_draft_stale", "Draft revision changed before Preflight"
                )
            try:
                compiled = await self._studio.bundle(
                    preview.tenant_id, preview.requested_by, preview.draft_id
                )
            except DraftCompilationError as error:
                raise PreflightCheckError(
                    "preflight_draft_not_ready",
                    "Draft no longer passes the production package gate",
                ) from error
            if (
                compiled.report.snapshot.content_hash != preview.content_hash
                or compiled.report.package_hash != preview.package_hash
            ):
                raise PreflightCheckError(
                    "preflight_bundle_drift", "Compiled Bundle no longer matches Preview hashes"
                )
            manifest = compiled.report.snapshot.manifest
            return PreflightEvidence(
                summary="Immutable Draft Bundle matched Preview",
                details={
                    "draftRevision": draft.revision,
                    "skillCount": len(compiled.report.snapshot.skill_snapshots),
                },
            )

        async def provision_check() -> PreflightEvidence:
            nonlocal handle
            now = self._clock()
            run = Run(
                run_id=f"preflight-{preview.preview_id}",
                session_id=f"preflight-{preview.preview_id}",
                tenant_id=preview.tenant_id,
                status=RunStatus.PROVISIONING,
                idempotency_key=preview.idempotency_key,
                created_at=now,
                updated_at=now,
                fencing_token=preview.fencing_token,
                input={"preflight": True},
            )
            handle = await self._sandbox.provision(run)
            profile = next(
                (
                    item
                    for item in default_capability_catalog().execution_profiles
                    if item.profile_id == preview.execution_profile
                    and item.version == preview.execution_profile_version
                ),
                None,
            )
            actual_provider = (
                "gvisor" if handle.provider == "kubernetes-gvisor" else handle.provider
            )
            if self._enforce_execution_profile_provider and (
                profile is None or profile.sandbox_provider != actual_provider
            ):
                raise PreflightCheckError(
                    "execution_profile_sandbox_provider_mismatch",
                    "Target Sandbox does not match the pinned Execution Profile",
                )
            return PreflightEvidence(
                summary="Target Sandbox provisioned",
                details={
                    "provider": handle.provider,
                    "isolation": handle.isolation_level.value,
                },
            )

        async def prepare_check() -> PreflightEvidence:
            assert handle is not None
            input_path = handle.path / ".harness-preflight" / "input.txt"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text("input-ready\n", encoding="utf-8")
            await self._sandbox.prepare(handle)
            return PreflightEvidence(
                summary="Workspace input staged in target Sandbox",
                details={"inputFile": True},
            )

        async def model_check() -> PreflightEvidence:
            assert manifest is not None and handle is not None
            return await self._model_probe.verify(
                preview.tenant_id, manifest, self._sandbox, handle
            )

        async def mcp_check() -> PreflightEvidence:
            assert draft is not None and manifest is not None and handle is not None
            identity = ExecutionIdentity(
                tenant_id=preview.tenant_id,
                user_id=preview.requested_by,
                project_id=draft.spec.name,
                session_id=f"preflight-{preview.preview_id}",
                run_id=f"preflight-{preview.preview_id}",
                agent_name=draft.spec.name,
                agent_version=draft.spec.version,
            )
            return await self._mcp_probe.verify(manifest, identity, self._sandbox, handle)

        async def approval_check() -> PreflightEvidence:
            assert (
                draft is not None
                and compiled is not None
                and manifest is not None
                and handle is not None
            )
            policy = (
                (
                    await self._policy_resolver(
                        preview.tenant_id,
                        manifest.spec.permissions.policy,
                    )
                ).call_policy
                if self._policy_resolver is not None
                else self._policies.resolve(manifest.spec.permissions.policy)
            )
            directory = compiled.report.snapshot.tool_directory
            declared = (
                {entry.name for entry in directory.entries}
                if directory is not None
                else {tool.builtin for tool in manifest.spec.tools if tool.builtin is not None}
            )
            decisions: dict[str, str | int | bool] = {}
            for tool_name in sorted(declared):
                result = policy.evaluate(
                    PolicyContext(
                        tenant_id=preview.tenant_id,
                        agent_name=draft.spec.name,
                        tool_name=tool_name,
                        arguments=(
                            {"command": "printf preflight"}
                            if tool_name == "Bash"
                            else (
                                {"file_path": "output/preflight.txt"}
                                if tool_name in {"Read", "Write", "Edit"}
                                else {}
                            )
                        ),
                        sandbox_isolation=handle.isolation_level,
                    )
                )
                decisions[tool_name] = result.decision.value
                if result.decision is PolicyDecision.DENY:
                    raise PreflightCheckError(
                        "approval_policy_mismatch",
                        f"Declared tool {tool_name} is denied by its permission profile",
                    )
                if tool_name == "Bash" and result.decision is not PolicyDecision.ALLOW:
                    raise PreflightCheckError(
                        "approval_policy_mismatch",
                        "Declared Bash must run without routine approval inside the Sandbox",
                    )
            return PreflightEvidence(
                summary="Declared tool permission coverage passed",
                details=decisions,
            )

        async def workspace_artifact_check() -> PreflightEvidence:
            nonlocal artifact
            assert handle is not None
            command = (
                "set -eu; "
                'test "$(cat .harness-preflight/input.txt)" = input-ready; '
                "mkdir -p output; "
                "printf 'write-ready\\n' > output/preflight.txt; "
                "printf 'edit-ready\\n' >> output/preflight.txt; "
                "printf 'bash-ready\\n' >> output/preflight.txt"
            )
            executed = await self._sandbox.execute(
                handle, ("bash", "-lc", command), timeout_seconds=30
            )
            if executed.exit_code != 0:
                raise PreflightCheckError(
                    "workspace_command_failed",
                    "Target Sandbox could not read, write and edit the workspace",
                )
            await self._sandbox.collect(handle)
            output = handle.path / "output" / "preflight.txt"
            if not output.is_file():
                raise PreflightCheckError(
                    "artifact_collect_failed", "Preflight Artifact was not collected"
                )
            content = output.read_bytes()
            if content != b"write-ready\nedit-ready\nbash-ready\n":
                raise PreflightCheckError(
                    "artifact_content_mismatch", "Preflight Artifact content did not match"
                )
            artifact = PreflightArtifactProof(
                name="preflight.txt",
                mediaType="text/plain",
                sha256=hashlib.sha256(content).hexdigest(),
                sizeBytes=len(content),
            )
            return PreflightEvidence(
                summary="Workspace read/write/edit/Bash and Artifact collection passed",
                details={"artifactBytes": len(content), "collected": True},
            )

        async def cleanup_check() -> PreflightEvidence:
            if handle is None:
                return PreflightEvidence(
                    summary="No Sandbox allocated",
                    details={"destroyed": False},
                    skipped=True,
                )
            await self._sandbox.destroy(handle)
            return PreflightEvidence(
                summary="Target Sandbox destroyed",
                details={"destroyed": True, "provider": handle.provider},
            )

        try:
            async with asyncio.timeout(self._timeout_seconds):
                await record(PreflightStage.BUNDLE, bundle_check)
                await record(PreflightStage.SANDBOX_PROVISION, provision_check)
                await record(PreflightStage.SANDBOX_PREPARE, prepare_check)
                await record(PreflightStage.MODEL, model_check)
                await record(PreflightStage.MCP, mcp_check)
                await record(PreflightStage.APPROVAL, approval_check)
                await record(PreflightStage.WORKSPACE_ARTIFACT, workspace_artifact_check)
        except _PreflightCancelledError:
            result_status = PreflightResultStatus.CANCELLED
            error_code = "preflight_cancelled"
        except TimeoutError:
            result_status = PreflightResultStatus.TIMED_OUT
            error_code = "preflight_timeout"
            if events and events[-1].event_type == "check.started":
                interrupted = events[-1]
                completed = self._clock()
                checks.append(
                    PreflightCheck(
                        stage=interrupted.stage,
                        status=PreflightCheckStatus.TIMED_OUT,
                        startedAt=interrupted.occurred_at,
                        completedAt=completed,
                        durationMs=_duration_ms(interrupted.occurred_at, completed),
                        summary="Preflight stage timed out",
                        errorCode="preflight_timeout",
                    )
                )
                events.append(
                    PreflightEvent(
                        sequence=len(events) + 1,
                        eventType="check.completed",
                        stage=interrupted.stage,
                        occurredAt=completed,
                        status=PreflightCheckStatus.TIMED_OUT,
                        errorCode="preflight_timeout",
                    )
                )
        except PreflightCheckError as failure:
            result_status = PreflightResultStatus.FAILED
            error_code = failure.error_code
        finally:
            try:
                await record(PreflightStage.CLEANUP, cleanup_check, honor_cancel=False)
            except PreflightCheckError:
                result_status = PreflightResultStatus.FAILED
                error_code = "cleanup_failed"

        return PreflightResult(
            previewId=preview.preview_id,
            status=result_status,
            startedAt=started_at,
            completedAt=self._clock(),
            checks=tuple(checks),
            events=tuple(events),
            errorCode=error_code,
            artifact=artifact,
        )


class LivePreflightProvisioner:
    """Persist the final Result before the Preview controller advances state."""

    def __init__(
        self,
        *,
        runner: LivePreflightRunner,
        repository: PreviewRepository,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def __call__(self, preview: PreviewDeployment) -> None:
        async def cancelled() -> bool:
            current = await self._repository.get(preview.tenant_id, preview.preview_id)
            return current.status is PreviewStatus.CANCELLING

        result = await self._runner.run(preview, cancelled=cancelled)
        await self._attach(preview.tenant_id, preview.preview_id, result)
        if result.status in {
            PreflightResultStatus.FAILED,
            PreflightResultStatus.TIMED_OUT,
        }:
            raise PreviewProvisioningError(result.error_code or "preflight_failed")

    async def _attach(self, tenant_id: str, preview_id: str, result: PreflightResult) -> None:
        for _attempt in range(4):
            current = await self._repository.get(tenant_id, preview_id)
            if current.status.is_terminal:
                return
            updated = current.model_copy(
                update={
                    "preflight_result": result,
                    "updated_at": self._clock(),
                    "fencing_token": current.fencing_token + 1,
                }
            )
            if await self._repository.compare_and_set(current.status, updated):
                return
        raise ConflictError(f"Preview changed while storing Preflight: {preview_id}")


def _duration_ms(started: datetime, completed: datetime) -> int:
    return max(0, int((completed - started).total_seconds() * 1000))
