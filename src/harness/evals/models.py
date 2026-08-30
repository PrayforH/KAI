"""Durable, tenant-scoped evaluation control-plane facts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from harness.evals.suite import EvalCase
from harness.studio.models import StudioModel


class EvalRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    PASSED = "passed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {
            EvalRunStatus.CANCELLED,
            EvalRunStatus.PASSED,
            EvalRunStatus.FAILED,
        }


class EvalCaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class EvalFixture(StudioModel):
    path: str = Field(min_length=1)
    media_type: str = Field(alias="mediaType", min_length=1)
    object_id: str = Field(alias="objectId", min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(alias="sizeBytes", ge=0)


class EvalDatasetVersion(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    dataset_id: str = Field(alias="datasetId", min_length=1)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    required: bool = True
    source_draft_id: str = Field(alias="sourceDraftId", min_length=1)
    source_draft_revision: int = Field(alias="sourceDraftRevision", ge=1)
    source_content_hash: str = Field(
        alias="sourceContentHash", pattern=r"^[a-f0-9]{64}$"
    )
    source_package_hash: str = Field(
        alias="sourcePackageHash", pattern=r"^[a-f0-9]{64}$"
    )
    cases: tuple[EvalCase, ...] = Field(min_length=1)
    fixtures: tuple[EvalFixture, ...] = ()
    created_by: str = Field(alias="createdBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def fixture_paths_are_unique(self) -> EvalDatasetVersion:
        paths = [item.path for item in self.fixtures]
        if len(paths) != len(set(paths)):
            raise ValueError("evaluation fixture paths must be unique")
        referenced = {item.path for case in self.cases for item in case.input_files}
        missing = sorted(referenced - set(paths))
        if missing:
            raise ValueError(f"evaluation fixtures are missing: {', '.join(missing)}")
        return self


class EvalOutputArtifact(StudioModel):
    artifact_id: str = Field(alias="artifactId", min_length=1)
    name: str = Field(min_length=1)
    media_type: str = Field(alias="mediaType", min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(alias="sizeBytes", ge=1)


class EvalRun(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    eval_run_id: str = Field(alias="evalRunId", min_length=1)
    dataset_id: str = Field(alias="datasetId", min_length=1)
    dataset_version: int = Field(alias="datasetVersion", ge=1)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    agent_version: str = Field(alias="agentVersion", min_length=1)
    preview_id: str | None = Field(default=None, alias="previewId")
    environment: str | None = None
    requested_by: str = Field(alias="requestedBy", min_length=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)
    status: EvalRunStatus
    fencing_token: int = Field(default=0, alias="fencingToken", ge=0)
    next_case_index: int = Field(default=0, alias="nextCaseIndex", ge=0)
    active_case_id: str | None = Field(default=None, alias="activeCaseId")
    active_session_id: str | None = Field(default=None, alias="activeSessionId")
    active_input_artifact_ids: tuple[str, ...] = Field(
        default=(), alias="activeInputArtifactIds"
    )
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    active_started_at: datetime | None = Field(default=None, alias="activeStartedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    error_code: str | None = Field(default=None, alias="errorCode")
    artifacts: tuple[EvalOutputArtifact, ...] = ()


class EvalCaseResult(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    eval_run_id: str = Field(alias="evalRunId", min_length=1)
    case_id: str = Field(alias="caseId", min_length=1)
    session_id: str = Field(default="", alias="sessionId")
    run_id: str = Field(default="", alias="runId")
    status: EvalCaseStatus
    passed: bool
    duration_seconds: float = Field(alias="durationSeconds", ge=0)
    failures: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    approval_requested: bool = Field(default=False, alias="approvalRequested")
    subagents: tuple[str, ...] = ()
    peak_concurrent_subagents: int = Field(
        default=0, alias="peakConcurrentSubagents", ge=0
    )
    completed_at: datetime = Field(alias="completedAt")


class EvalRunView(StudioModel):
    run: EvalRun
    cases: tuple[EvalCaseResult, ...] = ()
    passed_cases: int = Field(alias="passedCases", ge=0)
    total_cases: int = Field(alias="totalCases", ge=1)


class ImportedEvalCaseRequest(StudioModel):
    """One question-bank row before it becomes a durable EvalCase."""

    prompt: str = Field(min_length=1, max_length=4_000)
    tag: Literal["happy", "ambiguous", "safety"] = "happy"
    required_tools: tuple[str, ...] = Field(
        default=(), alias="requiredTools", max_length=20
    )
    forbidden_tools: tuple[str, ...] = Field(
        default=(), alias="forbiddenTools", max_length=20
    )
    terminal_statuses: tuple[str, ...] = Field(
        default=(), alias="terminalStatuses", max_length=6
    )


class ImportEvalDatasetRequest(StudioModel):
    draft_id: str = Field(alias="draftId", min_length=1)
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    name: str = Field(min_length=1, max_length=160)
    required: bool = True
    dataset_id: str | None = Field(default=None, alias="datasetId")
    format: Literal["json", "csv"]
    content: str = Field(min_length=1, max_length=256_000)


class CreateEvalDatasetVersionRequest(StudioModel):
    draft_id: str = Field(alias="draftId", min_length=1)
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    name: str = Field(min_length=1, max_length=160)
    dataset_id: str | None = Field(default=None, alias="datasetId")
    required: bool = True


class CreateEvalRunRequest(StudioModel):
    dataset_id: str = Field(alias="datasetId", min_length=1)
    dataset_version: int = Field(alias="datasetVersion", ge=1)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    agent_version: str = Field(alias="agentVersion", min_length=1)
    preview_id: str | None = Field(default=None, alias="previewId")
    environment: str | None = None
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)


class EvalGateResult(StudioModel):
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    passed: bool
    required_datasets: int = Field(alias="requiredDatasets", ge=0)
    passed_datasets: int = Field(alias="passedDatasets", ge=0)
    missing_dataset_ids: tuple[str, ...] = Field(alias="missingDatasetIds")


_ALLOWED_TRANSITIONS: dict[EvalRunStatus, frozenset[EvalRunStatus]] = {
    EvalRunStatus.QUEUED: frozenset(
        {EvalRunStatus.RUNNING, EvalRunStatus.CANCELLING}
    ),
    EvalRunStatus.RUNNING: frozenset(
        {EvalRunStatus.CANCELLING, EvalRunStatus.PASSED, EvalRunStatus.FAILED}
    ),
    EvalRunStatus.CANCELLING: frozenset({EvalRunStatus.CANCELLED}),
}


def transition_eval_run(current: EvalRunStatus, target: EvalRunStatus) -> EvalRunStatus:
    if current.is_terminal or target not in _ALLOWED_TRANSITIONS.get(
        current, frozenset()
    ):
        raise ValueError(f"invalid Eval Run transition: {current.value} -> {target.value}")
    return target
