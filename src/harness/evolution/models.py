"""Versioned evolution aggregates. Clients cannot submit scores or approval identities."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from harness.studio.models import AgentDraftSpec, StudioModel


class Patch(StudioModel):
    target: str = Field(pattern=r"^(systemPrompt|skill:[a-z][a-z0-9-]*)$")
    old_text: str = Field(alias="oldText", min_length=1, max_length=12000)
    new_text: str = Field(alias="newText", max_length=12000)
    reason: str = Field(min_length=1, max_length=2000)


class DatasetRef(StudioModel):
    dataset_id: str = Field(alias="datasetId", min_length=1)
    version: int = Field(ge=1)


class EvolutionBudget(StudioModel):
    max_candidates: int = Field(default=3, alias="maxCandidates", ge=1, le=5)
    max_trials: int = Field(default=2, alias="maxTrials", ge=1, le=5)
    max_cases: int = Field(default=100, alias="maxCases", ge=1, le=500)
    max_cost_usd: float = Field(default=10, alias="maxCostUsd", gt=0, le=100, allow_inf_nan=False)
    cost_reservation_usd: float = Field(
        default=0.1, alias="costReservationUsd", gt=0, le=10, allow_inf_nan=False
    )
    max_duration_seconds: int = Field(default=3600, alias="maxDurationSeconds", ge=60, le=86400)


class CreateEvolutionJob(StudioModel):
    draft_id: str = Field(alias="draftId", min_length=1)
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    objective: str = Field(min_length=10, max_length=2000)
    dataset: DatasetRef
    allowed_targets: tuple[str, ...] = Field(alias="allowedTargets", min_length=1, max_length=10)
    budget: EvolutionBudget = EvolutionBudget()
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=100)


class RevisionRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class ProposeCandidate(RevisionRequest):
    patches: tuple[Patch, ...] = Field(min_length=1, max_length=5)
    rationale: str = Field(min_length=1, max_length=2000)


class ReviewRequest(RevisionRequest):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=5, max_length=2000)
    report_hash: str = Field(alias="reportHash", pattern=r"^[a-f0-9]{64}$")


class Evidence(StudioModel):
    source_id: str = Field(alias="sourceId")
    kind: Literal["eval", "quality"]
    code: str
    detail: str


class Trial(StudioModel):
    trial_id: str = Field(alias="trialId")
    baseline_run_id: str | None = Field(default=None, alias="baselineRunId")
    candidate_run_id: str | None = Field(default=None, alias="candidateRunId")


class Comparison(StudioModel):
    status: Literal["passed", "regression", "insufficient_evidence"]
    report_hash: str = Field(alias="reportHash")
    case_count: int = Field(alias="caseCount")
    improved: tuple[str, ...] = ()
    regressed: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    unknown_cost_count: int = Field(default=0, alias="unknownCostCount")
    baseline_cost: float | None = Field(default=None, alias="baselineCost")
    candidate_cost: float | None = Field(default=None, alias="candidateCost")
    conclusion: str


class Review(StudioModel):
    reviewer: str
    decision: Literal["approve", "reject"]
    reason: str
    candidate_hash: str = Field(alias="candidateHash")
    report_hash: str = Field(alias="reportHash")
    policy_hash: str = Field(alias="policyHash")
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime = Field(alias="expiresAt")


class Candidate(StudioModel):
    candidate_id: str = Field(alias="candidateId")
    candidate_hash: str = Field(alias="candidateHash")
    manifest_hash: str = Field(alias="manifestHash")
    package_hash: str = Field(alias="packageHash")
    patches: tuple[Patch, ...]
    rationale: str
    diff: str
    spec: AgentDraftSpec
    preview_version: str = Field(alias="previewVersion")
    status: Literal[
        "proposed",
        "evaluating",
        "review_pending",
        "insufficient_evidence",
        "rejected",
        "approved",
        "releasing",
        "released",
        "cancelled",
        "rolled_back",
    ] = "proposed"
    trials: tuple[Trial, ...] = ()
    comparison: Comparison | None = None
    review: Review | None = None
    released_version: str | None = Field(default=None, alias="releasedVersion")
    created_at: datetime = Field(alias="createdAt")


class Experience(StudioModel):
    experience_id: str = Field(alias="experienceId")
    kind: Literal["task_lesson", "anti_pattern", "evolution_lesson"]
    content: str
    conditions: str
    source_candidate_id: str = Field(alias="sourceCandidateId")
    status: Literal["observed", "reviewed", "deprecated"] = "observed"
    version: int = 1
    expires_at: datetime = Field(alias="expiresAt")


class ExperienceRequest(RevisionRequest):
    kind: Literal["task_lesson", "anti_pattern", "evolution_lesson"]
    content: str = Field(min_length=10, max_length=2000)
    conditions: str = Field(min_length=5, max_length=1000)
    candidate_id: str = Field(alias="candidateId")


class HistoryEntry(StudioModel):
    action: str
    actor: str
    at: datetime
    resource: str = ""


class Observation(StudioModel):
    candidate_id: str = Field(alias="candidateId")
    agent_version: str = Field(alias="agentVersion")
    total_runs: int = Field(alias="totalRuns")
    succeeded_runs: int = Field(alias="succeededRuns")
    unknown_cost_runs: int = Field(alias="unknownCostRuns")
    feedback_count: int = Field(alias="feedbackCount")
    feedback_mean: float | None = Field(alias="feedbackMean")
    conclusion: str
    observed_at: datetime = Field(alias="observedAt")


class EvolutionJob(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    owner_id: str = Field(alias="ownerId")
    job_id: str = Field(alias="jobId")
    revision: int = 1
    request_hash: str = Field(alias="requestHash")
    agent_name: str = Field(alias="agentName")
    source_draft_id: str = Field(alias="sourceDraftId")
    objective: str
    baseline: AgentDraftSpec
    baseline_manifest_hash: str = Field(alias="baselineManifestHash")
    baseline_package_hash: str = Field(alias="baselinePackageHash")
    baseline_preview_version: str = Field(alias="baselinePreviewVersion")
    policy_hash: str = Field(alias="policyHash")
    dataset: DatasetRef
    dataset_hash: str = Field(alias="datasetHash")
    allowed_targets: tuple[str, ...] = Field(alias="allowedTargets")
    budget: EvolutionBudget
    status: Literal["active", "cancelled", "completed", "budget_exhausted"] = "active"
    candidates: tuple[Candidate, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    experiences: tuple[Experience, ...] = ()
    observations: tuple[Observation, ...] = ()
    history: tuple[HistoryEntry, ...] = ()
    # Retain the initial aggregate field for rolling upgrades; no automatic proposer is enabled.
    generation_attempts: int = Field(default=0, alias="generationAttempts")
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime = Field(alias="expiresAt")

    @model_validator(mode="after")
    def unique_candidates(self) -> EvolutionJob:
        ids = [c.candidate_id for c in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate")
        return self
