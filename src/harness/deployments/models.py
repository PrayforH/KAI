"""Immutable deployment snapshots and mutable environment pointers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field, model_validator

from harness.studio.models import NetworkAccess, StudioModel


class EnvironmentName(StrEnum):
    TEST = "test"
    CANARY = "canary"
    PRODUCTION = "production"


class DeploymentStatus(StrEnum):
    QUEUED = "queued"
    RECONCILING = "reconciling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED}


class DeploymentRoute(StudioModel):
    snapshot_id: str = Field(alias="snapshotId", min_length=1)
    weight: int = Field(ge=1, le=100)


class CredentialScope(StrEnum):
    USER = "user"
    TEAM = "team"
    WORKLOAD = "workload"


class EnvironmentQuotaBoundary(StudioModel):
    max_run_budget_usd: float | None = Field(
        default=None,
        alias="maxRunBudgetUsd",
        gt=0,
    )
    max_model_tokens: int | None = Field(
        default=None,
        alias="maxModelTokens",
        ge=1,
        le=10_000_000,
    )
    max_artifact_bytes: int | None = Field(
        default=None,
        alias="maxArtifactBytes",
        ge=1,
        le=10 * 1024 * 1024 * 1024,
    )


class EnvironmentResourcePolicy(StudioModel):
    execution_profile_id: str = Field(
        default="isolated-default",
        alias="executionProfileId",
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    execution_profile_version: int = Field(
        default=1,
        alias="executionProfileVersion",
        ge=1,
    )
    network_profile_id: str = Field(
        default="registered-mcp-only",
        alias="networkProfileId",
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    network_profile_version: int = Field(
        default=1,
        alias="networkProfileVersion",
        ge=1,
    )
    network_access: tuple[NetworkAccess, ...] = Field(
        default=(NetworkAccess.NONE, NetworkAccess.INTERNAL, NetworkAccess.EXTERNAL),
        alias="networkAccess",
    )
    allowed_model_routes: tuple[str, ...] = Field(
        default=(
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "new-api-default",
            "minimax-m3",
            "glm-5-3-flash",
        ),
        alias="allowedModelRoutes",
    )
    capability_catalog_revision: int = Field(
        default=1,
        alias="capabilityCatalogRevision",
        ge=1,
    )
    allowed_mcp_references: tuple[str, ...] = Field(
        default=("tavily-readonly",),
        alias="allowedMcpReferences",
    )
    allowed_knowledge_references: tuple[str, ...] = Field(
        default=(),
        alias="allowedKnowledgeReferences",
    )
    credential_scopes: tuple[CredentialScope, ...] = Field(
        default=(
            CredentialScope.USER,
            CredentialScope.TEAM,
            CredentialScope.WORKLOAD,
        ),
        alias="credentialScopes",
    )
    quota: EnvironmentQuotaBoundary = EnvironmentQuotaBoundary()

    @model_validator(mode="after")
    def unique_resource_ids(self) -> EnvironmentResourcePolicy:
        collections = (
            ("network access", self.network_access),
            ("model route", self.allowed_model_routes),
            ("MCP reference", self.allowed_mcp_references),
            ("knowledge reference", self.allowed_knowledge_references),
            ("credential scope", self.credential_scopes),
        )
        for label, identifiers in collections:
            if len(set(identifiers)) != len(identifiers):
                raise ValueError(f"duplicate {label} in Environment policy")
        if not self.allowed_model_routes:
            raise ValueError("Environment policy must allow at least one model route")
        if not self.credential_scopes:
            raise ValueError("Environment policy must allow at least one credential scope")
        for label, identifiers in (
            ("model route", self.allowed_model_routes),
            ("MCP reference", self.allowed_mcp_references),
            ("knowledge reference", self.allowed_knowledge_references),
        ):
            if any(not identifier.strip() for identifier in identifiers):
                raise ValueError(f"Environment policy contains an empty {label}")
        return self

    def digest(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class Environment(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    owner_user_id: str = Field(alias="ownerUserId", min_length=1)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    name: EnvironmentName
    revision: int = Field(ge=0)
    policy_revision: int = Field(default=1, alias="policyRevision", ge=1)
    resource_policy: EnvironmentResourcePolicy = Field(
        default_factory=EnvironmentResourcePolicy,
        alias="resourcePolicy",
    )
    routes: tuple[DeploymentRoute, ...] = ()
    healthy_snapshot_id: str | None = Field(default=None, alias="healthySnapshotId")
    updated_at: datetime = Field(alias="updatedAt")

    @computed_field(alias="policyHash")
    @property
    def policy_hash(self) -> str:
        return self.resource_policy.digest()

    @model_validator(mode="after")
    def valid_routes(self) -> Environment:
        if self.routes and sum(item.weight for item in self.routes) != 100:
            raise ValueError("deployment route weights must total 100")
        if len({item.snapshot_id for item in self.routes}) != len(self.routes):
            raise ValueError("deployment route snapshots must be unique")
        return self


class EnvironmentPolicySnapshot(StudioModel):
    environment: EnvironmentName
    environment_revision: int = Field(alias="environmentRevision", ge=0)
    policy_revision: int = Field(alias="policyRevision", ge=1)
    policy_hash: str = Field(alias="policyHash", pattern=r"^[a-f0-9]{64}$")
    resource_policy: EnvironmentResourcePolicy = Field(alias="resourcePolicy")
    captured_at: datetime = Field(alias="capturedAt")

    @model_validator(mode="after")
    def valid_policy_hash(self) -> EnvironmentPolicySnapshot:
        if self.policy_hash != self.resource_policy.digest():
            raise ValueError("Environment policy snapshot hash does not match its policy")
        return self


class ReplaceEnvironmentPolicyRequest(StudioModel):
    expected_environment_revision: int = Field(
        alias="expectedEnvironmentRevision",
        ge=0,
    )
    policy: EnvironmentResourcePolicy


class DeploymentSnapshot(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    snapshot_id: str = Field(alias="snapshotId", min_length=1)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    agent_version: str = Field(alias="agentVersion", min_length=1)
    environment: EnvironmentName
    manifest_hash: str = Field(alias="manifestHash", pattern=r"^[a-f0-9]{64}$")
    package_hash: str = Field(alias="packageHash", pattern=r"^[a-f0-9]{64}$")
    image_digest: str = Field(alias="imageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    execution_profile: str = Field(alias="executionProfile", min_length=1)
    execution_profile_version: int = Field(default=1, alias="executionProfileVersion", ge=1)
    execution_profile_hash: str = Field(
        default="0" * 64,
        alias="executionProfileHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    environment_policy_snapshot: EnvironmentPolicySnapshot | None = Field(
        default=None,
        alias="environmentPolicySnapshot",
    )
    config: dict[str, str | int | bool] = Field(default_factory=dict)
    eval_gate_passed: bool = Field(alias="evalGatePassed")
    eval_required_datasets: int = Field(alias="evalRequiredDatasets", ge=0)
    preview_id: str | None = Field(default=None, alias="previewId")
    created_by: str = Field(alias="createdBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")


class Deployment(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    deployment_id: str = Field(alias="deploymentId", min_length=1)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    environment: EnvironmentName
    action: Literal["promote", "rollback"]
    target_snapshot_id: str = Field(alias="targetSnapshotId", min_length=1)
    previous_snapshot_id: str | None = Field(default=None, alias="previousSnapshotId")
    canary_percent: int = Field(alias="canaryPercent", ge=1, le=100)
    expected_environment_revision: int = Field(alias="expectedEnvironmentRevision", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)
    requested_by: str = Field(alias="requestedBy", min_length=1)
    status: DeploymentStatus
    fencing_token: int = Field(default=0, alias="fencingToken", ge=0)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    error_code: str | None = Field(default=None, alias="errorCode")


class PromoteRequest(StudioModel):
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    agent_version: str = Field(alias="agentVersion", min_length=1)
    environment: EnvironmentName
    expected_environment_revision: int = Field(alias="expectedEnvironmentRevision", ge=0)
    canary_percent: int = Field(default=100, alias="canaryPercent", ge=1, le=100)
    image_digest: str = Field(alias="imageDigest", pattern=r"^sha256:[a-f0-9]{64}$")
    execution_profile: str = Field(alias="executionProfile", min_length=1)
    config: dict[str, str | int | bool] = Field(default_factory=dict)
    preview_id: str | None = Field(default=None, alias="previewId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)

    @model_validator(mode="after")
    def config_is_non_secret(self) -> PromoteRequest:
        forbidden = (
            "secret",
            "token",
            "password",
            "credential",
            "api_key",
            "apikey",
            "provider",
            "sandbox",
            "cpu",
            "memory",
            "disk",
            "network",
            "egress",
            "ttl",
        )
        unsafe = sorted(
            key for key in self.config if any(word in key.lower() for word in forbidden)
        )
        if unsafe:
            raise ValueError("deployment config contains secret-like or platform-managed keys")
        return self


class RollbackRequest(StudioModel):
    snapshot_id: str = Field(alias="snapshotId", min_length=1)
    expected_environment_revision: int = Field(alias="expectedEnvironmentRevision", ge=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=256)


class DeploymentView(StudioModel):
    deployment: Deployment
    target: DeploymentSnapshot
    environment: Environment


class DeploymentResolution(StudioModel):
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    environment: EnvironmentName
    snapshot_id: str = Field(alias="snapshotId")
    environment_policy_snapshot: EnvironmentPolicySnapshot = Field(
        alias="environmentPolicySnapshot"
    )
