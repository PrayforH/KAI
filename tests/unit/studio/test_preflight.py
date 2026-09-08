from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.config import Settings
from harness.core.models import Run
from harness.observability.provider import Observability, build_observability
from harness.policy.models import PolicyDecision, PolicyRule
from harness.policy.profiles import PolicyProfileRegistry, default_policy_profiles
from harness.policy.rules import PolicyEngine
from harness.sandbox.base import SandboxCommandResult, SandboxHandle
from harness.sandbox.local import LocalSandboxProvider
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.models import AgentTemplate, CreateAgentDraftRequest
from harness.studio.preflight import LivePreflightRunner
from harness.studio.preflight_models import (
    PreflightCheckStatus,
    PreflightResultStatus,
    PreflightStage,
)
from harness.studio.preflight_probes import (
    FakeMcpPreflightProbe,
    FakeModelPreflightProbe,
)
from harness.studio.preview_models import PreviewDeployment, PreviewStatus
from harness.studio.repositories import InMemoryAgentDraftRepository
from harness.studio.service import AgentStudioService

NOW = datetime(2026, 7, 16, 9, tzinfo=UTC)


class StageSandbox(LocalSandboxProvider):
    def __init__(self, root: Path, *, fail_stage: PreflightStage | None = None) -> None:
        super().__init__(root=root)
        self.fail_stage = fail_stage
        self.destroyed = False

    async def provision(self, run: Run) -> SandboxHandle:
        if self.fail_stage is PreflightStage.SANDBOX_PROVISION:
            raise RuntimeError("private provision failure")
        return await super().provision(run)

    async def prepare(self, handle: SandboxHandle) -> None:
        if self.fail_stage is PreflightStage.SANDBOX_PREPARE:
            raise RuntimeError("private prepare failure")
        await super().prepare(handle)

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        if self.fail_stage is PreflightStage.WORKSPACE_ARTIFACT:
            return SandboxCommandResult(exit_code=19, stderr="private command failure")
        return await super().execute(
            handle,
            argv,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        self.destroyed = True
        await super().destroy(handle)
        if self.fail_stage is PreflightStage.CLEANUP:
            raise RuntimeError("private cleanup failure")


async def context(
    tmp_path: Path,
    *,
    sandbox_failure: PreflightStage | None = None,
    model_failure: str | None = None,
    mcp_failure: str | None = None,
    policies: PolicyProfileRegistry | None = None,
    timeout_seconds: float = 30,
    model_delay: float = 0,
    observability: Observability | None = None,
    enforce_profile: bool = False,
) -> tuple[LivePreflightRunner, PreviewDeployment, StageSandbox]:
    catalog = default_capability_catalog()
    studio = AgentStudioService(
        InMemoryAgentDraftRepository(),
        AgentDraftCompiler(catalog),
        catalog,
        clock=lambda: NOW,
        id_generator=lambda: "draft_preflight",
    )
    draft = await studio.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=CreateAgentDraftRequest(
            name="preflight-operator",
            domain="preflight-check",
            displayName="Preflight Operator",
            description="Exercise every live Preflight boundary.",
            template=AgentTemplate.OPERATOR,
        ),
    )
    validation = await studio.validate("tenant-a", "builder", draft.draft_id)
    assert validation.ready
    assert validation.content_hash is not None
    assert validation.package_hash is not None
    preview = PreviewDeployment(
        previewId="preview-live",
        tenantId="tenant-a",
        draftId=draft.draft_id,
        draftRevision=draft.revision,
        contentHash=validation.content_hash,
        packageHash=validation.package_hash,
        requestedBy="builder",
        idempotencyKey="preflight-live",
        status=PreviewStatus.PROVISIONING,
        createdAt=NOW,
        updatedAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
    )
    sandbox = StageSandbox(tmp_path, fail_stage=sandbox_failure)
    runner = LivePreflightRunner(
        studio=studio,
        sandbox=sandbox,
        model_probe=FakeModelPreflightProbe(fail_code=model_failure, delay_seconds=model_delay),
        mcp_probe=FakeMcpPreflightProbe(fail_code=mcp_failure),
        policies=policies or default_policy_profiles(),
        observability=observability,
        timeout_seconds=timeout_seconds,
        clock=lambda: NOW,
        enforce_execution_profile_provider=enforce_profile,
    )
    return runner, preview, sandbox


async def never_cancelled() -> bool:
    return False


@pytest.mark.asyncio
async def test_preflight_rejects_sandbox_that_does_not_match_pinned_profile(
    tmp_path: Path,
) -> None:
    runner, preview, sandbox = await context(tmp_path, enforce_profile=True)

    result = await runner.run(preview, cancelled=never_cancelled)

    assert result.error_code == "execution_profile_sandbox_provider_mismatch"
    assert sandbox.destroyed


@pytest.mark.asyncio
async def test_local_development_profile_matches_explicit_local_preview(
    tmp_path: Path,
) -> None:
    runner, preview, sandbox = await context(tmp_path, enforce_profile=True)
    local_preview = preview.model_copy(update={"execution_profile": "local-development"})

    result = await runner.run(local_preview, cancelled=never_cancelled)

    assert result.status is PreflightResultStatus.PASSED
    provision = next(
        check for check in result.checks if check.stage is PreflightStage.SANDBOX_PROVISION
    )
    assert provision.details["provider"] == "local"
    assert sandbox.destroyed


@pytest.mark.asyncio
async def test_live_preflight_passes_every_boundary_and_collects_artifact(
    tmp_path: Path,
) -> None:
    runner, preview, sandbox = await context(tmp_path)

    result = await runner.run(preview, cancelled=never_cancelled)

    assert result.status is PreflightResultStatus.PASSED
    assert [check.stage for check in result.checks] == list(PreflightStage)
    assert all(
        check.status in {PreflightCheckStatus.PASSED, PreflightCheckStatus.SKIPPED}
        for check in result.checks
    )
    assert result.artifact is not None
    assert result.artifact.name == "preflight.txt"
    assert result.artifact.size_bytes == 34
    assert len(result.events) == len(result.checks) * 2
    assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
    assert sandbox.destroyed
    assert list(tmp_path.iterdir()) == []


def mismatched_policies() -> PolicyProfileRegistry:
    return PolicyProfileRegistry(
        {
            "production-standard": PolicyEngine(
                [
                    PolicyRule(
                        name="routine-bash-review",
                        tool="Bash",
                        decision=PolicyDecision.ASK,
                    ),
                    PolicyRule(name="write", tool="Write", decision=PolicyDecision.ALLOW),
                    PolicyRule(name="edit", tool="Edit", decision=PolicyDecision.ALLOW),
                ]
            )
        }
    )


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    [
        (PreflightStage.SANDBOX_PROVISION, "sandbox_provision_failed"),
        (PreflightStage.SANDBOX_PREPARE, "sandbox_prepare_failed"),
        (PreflightStage.WORKSPACE_ARTIFACT, "workspace_command_failed"),
        (PreflightStage.CLEANUP, "cleanup_failed"),
    ],
)
@pytest.mark.asyncio
async def test_fake_sandbox_covers_each_failure_stage(
    tmp_path: Path, stage: PreflightStage, expected_code: str
) -> None:
    runner, preview, sandbox = await context(tmp_path, sandbox_failure=stage)

    result = await runner.run(preview, cancelled=never_cancelled)

    assert result.status is PreflightResultStatus.FAILED
    assert result.error_code == expected_code
    assert expected_code in {
        check.error_code for check in result.checks if check.error_code is not None
    }
    assert sandbox.destroyed is (stage is not PreflightStage.SANDBOX_PROVISION)


@pytest.mark.parametrize(
    ("model_failure", "mcp_failure", "expected_stage", "expected_code"),
    [
        ("model_unreachable", None, PreflightStage.MODEL, "model_unreachable"),
        (None, "mcp_tool_mismatch", PreflightStage.MCP, "mcp_tool_mismatch"),
    ],
)
@pytest.mark.asyncio
async def test_fake_probes_preserve_stable_failure_codes_and_cleanup(
    tmp_path: Path,
    model_failure: str | None,
    mcp_failure: str | None,
    expected_stage: PreflightStage,
    expected_code: str,
) -> None:
    runner, preview, sandbox = await context(
        tmp_path, model_failure=model_failure, mcp_failure=mcp_failure
    )

    result = await runner.run(preview, cancelled=never_cancelled)

    assert result.status is PreflightResultStatus.FAILED
    assert result.error_code == expected_code
    failed_check = next(
        check for check in result.checks if check.status is PreflightCheckStatus.FAILED
    )
    assert failed_check.stage is expected_stage
    assert sandbox.destroyed
    assert "private" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_bundle_drift_and_approval_mismatch_are_fail_closed(tmp_path: Path) -> None:
    runner, preview, _sandbox = await context(tmp_path)
    drift = preview.model_copy(update={"content_hash": "f" * 64})
    drifted = await runner.run(drift, cancelled=never_cancelled)
    assert drifted.error_code == "preflight_bundle_drift"

    runner, preview, sandbox = await context(tmp_path / "approval", policies=mismatched_policies())
    rejected = await runner.run(preview, cancelled=never_cancelled)
    assert rejected.error_code == "approval_policy_mismatch"
    assert sandbox.destroyed


@pytest.mark.asyncio
async def test_cancel_and_timeout_are_terminal_and_always_cleanup(tmp_path: Path) -> None:
    runner, preview, sandbox = await context(tmp_path / "cancel", model_delay=1)
    calls = 0

    async def cancel_during_model() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 5

    cancelled = await runner.run(preview, cancelled=cancel_during_model)
    assert cancelled.status is PreflightResultStatus.CANCELLED
    assert cancelled.error_code == "preflight_cancelled"
    assert any(
        check.stage is PreflightStage.MODEL and check.status is PreflightCheckStatus.CANCELLED
        for check in cancelled.checks
    )
    assert sandbox.destroyed

    runner, preview, sandbox = await context(
        tmp_path / "timeout", timeout_seconds=0.5, model_delay=1
    )
    timed_out = await runner.run(preview, cancelled=never_cancelled)
    assert timed_out.status is PreflightResultStatus.TIMED_OUT
    assert timed_out.error_code == "preflight_timeout"
    assert any(check.status is PreflightCheckStatus.TIMED_OUT for check in timed_out.checks)
    assert sandbox.destroyed


@pytest.mark.asyncio
async def test_preflight_trace_exports_only_allowlisted_stage_facts(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    observability = build_observability(
        Settings(otel_enabled=True, otlp_endpoint="http://unused/v1/traces"),
        exporter=exporter,
        processor_factory=SimpleSpanProcessor,
    )
    runner, preview, _sandbox = await context(tmp_path, observability=observability)

    result = await runner.run(preview, cancelled=never_cancelled)

    assert result.status is PreflightResultStatus.PASSED
    spans = exporter.get_finished_spans()
    assert len(spans) == len(PreflightStage)
    for span in spans:
        assert set(span.attributes or {}) == {
            "harness.preview.id",
            "harness.preflight.stage",
        }
    serialized = repr(spans)
    assert "systemPrompt" not in serialized
    assert "credential" not in serialized
