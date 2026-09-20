from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from harness.studio.models import StudioModel


class ScoreSource(StrEnum):
    RULE = "rule"
    HUMAN = "human"
    LLM_JUDGE = "llm_judge"


class QualitySyncStatus(StrEnum):
    QUEUED = "queued"
    SYNCING = "syncing"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {QualitySyncStatus.SUCCEEDED, QualitySyncStatus.FAILED}


class AlertState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class QualityScore(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    score_id: str = Field(alias="scoreId")
    run_id: str = Field(alias="runId")
    trace_id: str | None = Field(default=None, alias="traceId", pattern=r"^[a-f0-9]{32}$")
    session_id: str = Field(alias="sessionId")
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    deployment_snapshot_id: str | None = Field(default=None, alias="deploymentSnapshotId")
    eval_run_id: str | None = Field(default=None, alias="evalRunId")
    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    value: float | None = Field(default=None, ge=0, le=1)
    source: ScoreSource
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")


class HumanFeedbackRequest(StudioModel):
    value: float = Field(ge=0, le=1)


class AlertRule(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    rule_id: str = Field(alias="ruleId")
    agent_name: str = Field(alias="agentName")
    score_name: str = Field(alias="scoreName")
    minimum_value: float = Field(alias="minimumValue", ge=0, le=1)
    minimum_samples: int = Field(default=1, alias="minimumSamples", ge=1)
    blocks_promotion: bool = Field(default=True, alias="blocksPromotion")
    enabled: bool = True
    dashboard_url: str | None = Field(default=None, alias="dashboardUrl")
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def llm_judge_cannot_block_alone(self) -> AlertRule:
        if self.blocks_promotion and self.score_name.startswith("llm_judge"):
            raise ValueError("LLM Judge rules cannot directly block promotion")
        return self


class CreateAlertRuleRequest(StudioModel):
    agent_name: str = Field(alias="agentName")
    score_name: str = Field(alias="scoreName", pattern=r"^[a-z][a-z0-9_.-]*$")
    minimum_value: float = Field(alias="minimumValue", ge=0, le=1)
    minimum_samples: int = Field(default=1, alias="minimumSamples", ge=1)
    blocks_promotion: bool = Field(default=True, alias="blocksPromotion")
    dashboard_url: str | None = Field(default=None, alias="dashboardUrl")

    @model_validator(mode="after")
    def llm_judge_cannot_block_alone(self) -> CreateAlertRuleRequest:
        if self.blocks_promotion and self.score_name.startswith("llm_judge"):
            raise ValueError("LLM Judge rules cannot directly block promotion")
        return self


class AlertIncident(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    incident_id: str = Field(alias="incidentId")
    rule_id: str = Field(alias="ruleId")
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    owner_user_id: str = Field(alias="ownerUserId")
    state: AlertState
    observed_value: float = Field(alias="observedValue", ge=0, le=1)
    sample_count: int = Field(alias="sampleCount", ge=1)
    opened_at: datetime = Field(alias="openedAt")
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")


class QualitySyncJob(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    sync_id: str = Field(alias="syncId")
    kind: str = Field(pattern="^(score|dataset)$")
    resource_id: str = Field(alias="resourceId")
    status: QualitySyncStatus
    attempts: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, alias="errorCode")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class DatasetProjection(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    projection_id: str = Field(alias="projectionId")
    dataset_id: str = Field(alias="datasetId")
    dataset_version: int = Field(alias="datasetVersion", ge=1)
    name: str
    agent_name: str = Field(alias="agentName")
    case_count: int = Field(alias="caseCount", ge=1)
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")


class QualityGateResult(StudioModel):
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    passed: bool
    blocking_incident_ids: tuple[str, ...] = Field(alias="blockingIncidentIds")
