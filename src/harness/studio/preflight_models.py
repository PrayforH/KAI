"""Versioned, secret-free facts emitted by Studio live Preflight."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from harness.studio.models import StudioModel

PREFLIGHT_SCHEMA_VERSION = "harness.preflight/v1"


class PreflightStage(StrEnum):
    BUNDLE = "bundle"
    SANDBOX_PROVISION = "sandbox_provision"
    SANDBOX_PREPARE = "sandbox_prepare"
    MODEL = "model"
    MCP = "mcp"
    APPROVAL = "approval"
    WORKSPACE_ARTIFACT = "workspace_artifact"
    CLEANUP = "cleanup"


class PreflightCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class PreflightResultStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class PreflightCheck(StudioModel):
    stage: PreflightStage
    status: PreflightCheckStatus
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime = Field(alias="completedAt")
    duration_ms: int = Field(alias="durationMs", ge=0)
    summary: str = Field(min_length=1, max_length=240)
    error_code: str | None = Field(default=None, alias="errorCode")
    details: dict[str, str | int | bool] = Field(default_factory=dict)


class PreflightEvent(StudioModel):
    sequence: int = Field(ge=1)
    event_type: Literal["check.started", "check.completed"] = Field(alias="eventType")
    stage: PreflightStage
    occurred_at: datetime = Field(alias="occurredAt")
    status: PreflightCheckStatus | None = None
    error_code: str | None = Field(default=None, alias="errorCode")


class PreflightArtifactProof(StudioModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(alias="mediaType", min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(alias="sizeBytes", ge=1)


class PreflightResult(StudioModel):
    schema_version: Literal["harness.preflight/v1"] = Field(
        default=PREFLIGHT_SCHEMA_VERSION, alias="schemaVersion"
    )
    preview_id: str = Field(alias="previewId", min_length=1)
    status: PreflightResultStatus
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime = Field(alias="completedAt")
    checks: tuple[PreflightCheck, ...]
    events: tuple[PreflightEvent, ...]
    error_code: str | None = Field(default=None, alias="errorCode")
    artifact: PreflightArtifactProof | None = None
    # Optional for historical records; required for new production promotions.
    policy_id: str | None = Field(default=None, alias="policyId")
    policy_revision: int | None = Field(default=None, alias="policyRevision")
    policy_hash: str | None = Field(default=None, alias="policyHash")
    package_hash: str | None = Field(default=None, alias="packageHash")
    execution_profile_hash: str | None = Field(default=None, alias="executionProfileHash")
