"""Promotion and rollback use cases over immutable snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from harness.auth.audit import AuditService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.core.ports import AgentRegistry
from harness.deployments.models import (
    CredentialScope,
    Deployment,
    DeploymentResolution,
    DeploymentSnapshot,
    DeploymentStatus,
    DeploymentView,
    Environment,
    EnvironmentName,
    EnvironmentPolicySnapshot,
    EnvironmentQuotaBoundary,
    EnvironmentResourcePolicy,
    PromoteRequest,
    ReplaceEnvironmentPolicyRequest,
    RollbackRequest,
)
from harness.deployments.queue import DeploymentTask, DeploymentTaskQueue
from harness.deployments.repositories import DeploymentRepository, EnvironmentRepository
from harness.evals.service import EvalControlPlaneService
from harness.quota.models import QuotaResource, ResourceReservation
from harness.quota.service import QuotaService
from harness.studio.catalog import default_capability_catalog
from harness.studio.models import CapabilityCatalogRecord, ExecutionProfileMetadata
from harness.studio.preview_service import PreviewService

QualityGate = Callable[[str, str, str, str], Awaitable[object]]
ExecutionProfileResolver = Callable[[str, str], Awaitable[ExecutionProfileMetadata]]
CapabilityCatalogResolver = Callable[[str], Awaitable[CapabilityCatalogRecord]]
KnowledgeReferenceValidator = Callable[[str, tuple[str, ...]], Awaitable[None]]


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class DeploymentService:
    def __init__(
        self,
        *,
        environments: EnvironmentRepository,
        deployments: DeploymentRepository,
        queue: DeploymentTaskQueue,
        registry: AgentRegistry,
        evals: EvalControlPlaneService,
        previews: PreviewService | None = None,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
        quality_gate: QualityGate | None = None,
        execution_profile_resolver: ExecutionProfileResolver | None = None,
        capability_catalog_resolver: CapabilityCatalogResolver | None = None,
        knowledge_reference_validator: KnowledgeReferenceValidator | None = None,
        quotas: QuotaService | None = None,
    ) -> None:
        self._environments = environments
        self._deployments = deployments
        self._queue = queue
        self._registry = registry
        self._evals = evals
        self._previews = previews
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = id_generator or _id
        self._quality_gate = quality_gate
        self._execution_profile_resolver = execution_profile_resolver
        self._capability_catalog_resolver = capability_catalog_resolver
        self._knowledge_reference_validator = knowledge_reference_validator
        self._quotas = quotas

    async def _catalog(self, tenant_id: str) -> CapabilityCatalogRecord:
        if self._capability_catalog_resolver is not None:
            return await self._capability_catalog_resolver(tenant_id)
        return CapabilityCatalogRecord(
            tenantId=tenant_id,
            revision=1,
            catalog=default_capability_catalog(),
            updatedBy="system",
            updatedAt=self._clock(),
        )

    async def _execution_profile(
        self,
        tenant_id: str,
        profile_id: str,
        *,
        catalog: CapabilityCatalogRecord | None = None,
    ) -> ExecutionProfileMetadata:
        if self._execution_profile_resolver is not None:
            return await self._execution_profile_resolver(tenant_id, profile_id)
        catalog_record = catalog or await self._catalog(tenant_id)
        profile = next(
            (
                item
                for item in catalog_record.catalog.execution_profiles
                if item.profile_id == profile_id
            ),
            None,
        )
        if profile is None or not profile.enabled:
            raise ConflictError(f"Execution Profile is unavailable: {profile_id}")
        return profile

    async def _validate_knowledge_references(
        self,
        tenant_id: str,
        references: tuple[str, ...],
    ) -> None:
        if references and self._knowledge_reference_validator is not None:
            await self._knowledge_reference_validator(tenant_id, references)

    @staticmethod
    def _policy_snapshot(
        environment: Environment,
        *,
        captured_at: datetime,
    ) -> EnvironmentPolicySnapshot:
        return EnvironmentPolicySnapshot(
            environment=environment.name,
            environmentRevision=environment.revision,
            policyRevision=environment.policy_revision,
            policyHash=environment.policy_hash,
            resourcePolicy=environment.resource_policy,
            capturedAt=captured_at,
        )

    @staticmethod
    def _default_policy(
        name: EnvironmentName,
        catalog: CapabilityCatalogRecord,
    ) -> EnvironmentResourcePolicy:
        eligible_profiles = [
            item
            for item in catalog.catalog.execution_profiles
            if item.enabled and (name is not EnvironmentName.PRODUCTION or item.production_allowed)
        ]
        preferred = next(
            (item for item in eligible_profiles if item.profile_id == "isolated-default"),
            eligible_profiles[0] if eligible_profiles else None,
        )
        if preferred is None:
            raise ConflictError(f"No Execution Profile is available for {name.value}")
        enabled_mcp = {item.reference for item in catalog.catalog.mcp_servers if item.enabled}
        budget, tokens, artifacts = {
            EnvironmentName.TEST: (5.0, 500_000, 100 * 1024 * 1024),
            EnvironmentName.CANARY: (2.0, 300_000, 50 * 1024 * 1024),
            EnvironmentName.PRODUCTION: (1.0, 200_000, 25 * 1024 * 1024),
        }[name]
        return EnvironmentResourcePolicy(
            executionProfileId=preferred.profile_id,
            executionProfileVersion=preferred.version,
            networkProfileId=preferred.network_policy_id,
            networkProfileVersion=preferred.version,
            networkAccess=preferred.network_access,
            allowedModelRoutes=tuple(
                item.route_id for item in catalog.catalog.model_routes if item.enabled
            ),
            capabilityCatalogRevision=catalog.revision,
            allowedMcpReferences=tuple(
                reference
                for reference in preferred.allowed_mcp_references
                if reference in enabled_mcp
            ),
            allowedKnowledgeReferences=(),
            credentialScopes=(
                CredentialScope.USER,
                CredentialScope.TEAM,
                CredentialScope.WORKLOAD,
            ),
            quota=EnvironmentQuotaBoundary(
                maxRunBudgetUsd=budget,
                maxModelTokens=tokens,
                maxArtifactBytes=artifacts,
            ),
        )

    @staticmethod
    def _validate_policy_catalog(
        policy: EnvironmentResourcePolicy,
        catalog: CapabilityCatalogRecord,
    ) -> ExecutionProfileMetadata:
        if policy.capability_catalog_revision != catalog.revision:
            raise ConflictError(
                "Environment policy capability catalog revision is stale: "
                f"expected={catalog.revision} actual={policy.capability_catalog_revision}"
            )
        profile = next(
            (
                item
                for item in catalog.catalog.execution_profiles
                if item.profile_id == policy.execution_profile_id
            ),
            None,
        )
        if (
            profile is None
            or not profile.enabled
            or profile.version != policy.execution_profile_version
        ):
            raise ConflictError("Environment policy references an unavailable Execution Profile")
        if (
            policy.network_profile_id != profile.network_policy_id
            or policy.network_profile_version != profile.version
            or set(policy.network_access).difference(profile.network_access)
        ):
            raise ConflictError(
                "Environment network profile does not match the reviewed Execution Profile"
            )
        available_routes = {item.route_id for item in catalog.catalog.model_routes if item.enabled}
        missing_routes = sorted(set(policy.allowed_model_routes) - available_routes)
        if missing_routes:
            raise ConflictError(
                "Environment policy references unavailable model routes: "
                + ", ".join(missing_routes)
            )
        available_mcp = {item.reference for item in catalog.catalog.mcp_servers if item.enabled}
        missing_mcp = sorted(set(policy.allowed_mcp_references) - available_mcp)
        if missing_mcp:
            raise ConflictError(
                "Environment policy references unavailable MCP resources: " + ", ".join(missing_mcp)
            )
        profile_denied = sorted(
            set(policy.allowed_mcp_references) - set(profile.allowed_mcp_references)
        )
        if profile_denied:
            raise ConflictError(
                "Environment MCP resources exceed the Execution Profile: "
                + ", ".join(profile_denied)
            )
        return profile

    @staticmethod
    def _validate_agent_policy(
        version: AgentVersion,
        policy: EnvironmentResourcePolicy,
        *,
        execution_profile: str,
        execution_profile_version: int,
    ) -> None:
        if (
            execution_profile != policy.execution_profile_id
            or execution_profile_version != policy.execution_profile_version
        ):
            raise ConflictError("Deployment Execution Profile is outside the Environment policy")
        published_snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
        if (
            published_snapshot.tool_directory is not None
            and published_snapshot.tool_directory.catalog_revision
            != policy.capability_catalog_revision
        ):
            raise ConflictError(
                "Agent tool directory catalog revision is outside the Environment policy"
            )
        manifest = published_snapshot.manifest
        model_routes = {
            manifest.spec.model.route,
            *((manifest.spec.model.fallback_route,) if manifest.spec.model.fallback_route else ()),
        }
        denied_routes = sorted(model_routes - set(policy.allowed_model_routes))
        if denied_routes:
            raise ConflictError(
                "Agent model routes are outside the Environment policy: " + ", ".join(denied_routes)
            )
        mcp_references = {tool.mcp for tool in manifest.spec.tools if tool.mcp is not None}
        denied_mcp = sorted(mcp_references - set(policy.allowed_mcp_references))
        if denied_mcp:
            raise ConflictError(
                "Agent MCP resources are outside the Environment policy: " + ", ".join(denied_mcp)
            )
        denied_knowledge = sorted(
            set(manifest.spec.knowledge_references) - set(policy.allowed_knowledge_references)
        )
        if denied_knowledge:
            raise ConflictError(
                "Agent Knowledge Bases are outside the Environment policy: "
                + ", ".join(denied_knowledge)
            )

    async def environment(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        name: EnvironmentName,
    ) -> Environment:
        try:
            return await self._environments.get(tenant_id, owner_user_id, agent_name, name)
        except NotFoundError:
            catalog = await self._catalog(tenant_id)
            value = Environment(
                tenantId=tenant_id,
                ownerUserId=owner_user_id,
                agentName=agent_name,
                name=name,
                revision=0,
                policyRevision=1,
                resourcePolicy=self._default_policy(name, catalog),
                updatedAt=self._clock(),
            )
            try:
                await self._environments.add(value)
            except ConflictError:
                return await self._environments.get(tenant_id, owner_user_id, agent_name, name)
            return value

    async def list_environments(
        self, tenant_id: str, owner_user_id: str, agent_name: str
    ) -> list[Environment]:
        existing = {
            item.name: item
            for item in await self._environments.list_for_agent(
                tenant_id, owner_user_id, agent_name
            )
        }
        return [
            existing.get(name) or await self.environment(tenant_id, owner_user_id, agent_name, name)
            for name in EnvironmentName
        ]

    async def replace_environment_policy(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_name: str,
        environment_name: EnvironmentName,
        request: ReplaceEnvironmentPolicyRequest,
    ) -> Environment:
        current = await self.environment(tenant_id, user_id, agent_name, environment_name)
        if current.revision != request.expected_environment_revision:
            raise ConflictError("Environment revision changed before policy update")
        catalog = await self._catalog(tenant_id)
        profile = self._validate_policy_catalog(request.policy, catalog)
        await self._validate_knowledge_references(
            tenant_id,
            request.policy.allowed_knowledge_references,
        )
        if environment_name is EnvironmentName.PRODUCTION and not profile.production_allowed:
            raise ConflictError("Production Environment requires a production-enabled Profile")
        for route in current.routes:
            snapshot = await self._deployments.get_snapshot_for_user(
                tenant_id, user_id, route.snapshot_id
            )
            version = await self._registry.get(
                tenant_id,
                snapshot.created_by,
                snapshot.agent_name,
                snapshot.agent_version,
            )
            self._validate_agent_policy(
                version,
                request.policy,
                execution_profile=snapshot.execution_profile,
                execution_profile_version=snapshot.execution_profile_version,
            )
        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "policy_revision": current.policy_revision + 1,
                "resource_policy": request.policy,
                "updated_at": self._clock(),
            }
        )
        if not await self._environments.compare_and_set(current.revision, updated):
            raise ConflictError("Environment revision changed before policy update")
        if self._audit:
            await self._audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.environment.policy.replace",
                resource_type="environment",
                resource_id=f"{agent_name}:{environment_name.value}",
                outcome="success",
                details={
                    "agent_name": agent_name,
                    "environment": environment_name.value,
                    "policy_revision": updated.policy_revision,
                    "policy_hash": updated.policy_hash,
                    "catalog_revision": request.policy.capability_catalog_revision,
                },
            )
        return updated

    async def promote(
        self, *, tenant_id: str, user_id: str, request: PromoteRequest
    ) -> DeploymentView:
        existing = await self._deployments.find_by_idempotency(
            tenant_id, user_id, request.idempotency_key
        )
        if existing is not None:
            return await self._same_promote(existing, request)
        environment = await self.environment(
            tenant_id, user_id, request.agent_name, request.environment
        )
        if environment.revision != request.expected_environment_revision:
            raise ConflictError("Environment revision changed before promotion")
        version = await self._registry.get(
            tenant_id, user_id, request.agent_name, request.agent_version
        )
        if version.status is not AgentVersionStatus.PUBLISHED or not version.package_hash:
            raise ConflictError("Promotion requires an immutable published Agent package")
        gate = await self._evals.require_promotion_allowed(
            tenant_id, user_id, request.agent_name, request.agent_version
        )
        if self._quality_gate is not None:
            await self._quality_gate(tenant_id, user_id, request.agent_name, request.agent_version)
        if request.preview_id:
            if self._previews is None:
                raise ConflictError("Preview verification is unavailable")
            preview = await self._previews.get(tenant_id, user_id, request.preview_id)
            if (
                preview.stale
                or preview.status.value != "ready"
                or preview.package_hash != version.package_hash
            ):
                raise ConflictError("Promotion Preview does not prove this Agent package")
        catalog = await self._catalog(tenant_id)
        policy_profile = self._validate_policy_catalog(
            environment.resource_policy,
            catalog,
        )
        await self._validate_knowledge_references(
            tenant_id,
            environment.resource_policy.allowed_knowledge_references,
        )
        profile = await self._execution_profile(
            tenant_id,
            request.execution_profile,
            catalog=catalog,
        )
        if not profile.enabled:
            raise ConflictError("Execution Profile is disabled")
        if request.environment is EnvironmentName.PRODUCTION and not profile.production_allowed:
            raise ConflictError("Execution Profile is not enabled for production")
        if (
            profile.profile_id != policy_profile.profile_id
            or profile.version != policy_profile.version
            or profile.network_policy_id != policy_profile.network_policy_id
            or set(environment.resource_policy.network_access).difference(profile.network_access)
        ):
            raise ConflictError("Deployment Execution Profile is outside the Environment policy")
        self._validate_agent_policy(
            version,
            environment.resource_policy,
            execution_profile=profile.profile_id,
            execution_profile_version=profile.version,
        )
        profile_payload = profile.model_dump(mode="json", by_alias=True)
        profile_hash = hashlib.sha256(
            json.dumps(
                profile_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        snapshot = DeploymentSnapshot(
            tenantId=tenant_id,
            snapshotId=self._ids("deployment_snapshot"),
            agentName=request.agent_name,
            agentVersion=request.agent_version,
            environment=request.environment,
            manifestHash=version.manifest_hash,
            packageHash=version.package_hash,
            imageDigest=request.image_digest,
            executionProfile=request.execution_profile,
            executionProfileVersion=profile.version,
            executionProfileHash=profile_hash,
            environmentPolicySnapshot=self._policy_snapshot(
                environment,
                captured_at=self._clock(),
            ),
            config=request.config,
            evalGatePassed=gate.passed,
            evalRequiredDatasets=gate.required_datasets,
            previewId=request.preview_id,
            createdBy=user_id,
            createdAt=self._clock(),
        )
        reservation: ResourceReservation | None = None
        if self._quotas is not None:
            reservation = await self._quotas.reserve(
                tenant_id=tenant_id,
                resource=QuotaResource.DEPLOYMENT_PROMOTIONS,
                amount=1,
                subject_id=f"deployment:{user_id}:{request.idempotency_key}",
                idempotency_key=(f"deployment:{user_id}:{request.idempotency_key}:promotion"),
                agent_name=request.agent_name,
                environment=request.environment.value,
            )
        try:
            await self._deployments.add_snapshot(snapshot)
        except Exception:
            if reservation is not None and self._quotas is not None:
                await self._quotas.release(reservation)
            raise
        deployment = Deployment(
            tenantId=tenant_id,
            deploymentId=self._ids("deployment"),
            agentName=request.agent_name,
            environment=request.environment,
            action="promote",
            targetSnapshotId=snapshot.snapshot_id,
            previousSnapshotId=environment.healthy_snapshot_id,
            canaryPercent=request.canary_percent,
            expectedEnvironmentRevision=environment.revision,
            idempotencyKey=request.idempotency_key,
            requestedBy=user_id,
            status=DeploymentStatus.QUEUED,
            createdAt=self._clock(),
            updatedAt=self._clock(),
        )
        try:
            await self._deployments.add(deployment)
        except ConflictError:
            concurrent = await self._deployments.find_by_idempotency(
                tenant_id, user_id, request.idempotency_key
            )
            if concurrent is None:
                if reservation is not None and self._quotas is not None:
                    await self._quotas.release(reservation)
                raise
            if reservation is not None and self._quotas is not None:
                await self._quotas.commit(reservation)
            return await self._same_promote(concurrent, request)
        except Exception:
            if reservation is not None and self._quotas is not None:
                await self._quotas.release(reservation)
            raise
        await self._queue.enqueue(
            DeploymentTask(tenant_id=tenant_id, deployment_id=deployment.deployment_id)
        )
        await self._record(deployment, user_id, "studio.deployment.promote")
        if reservation is not None and self._quotas is not None:
            await self._quotas.commit(reservation)
        return DeploymentView(deployment=deployment, target=snapshot, environment=environment)

    async def rollback(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_name: str,
        environment_name: EnvironmentName,
        request: RollbackRequest,
    ) -> DeploymentView:
        existing = await self._deployments.find_by_idempotency(
            tenant_id, user_id, request.idempotency_key
        )
        if existing is not None:
            target = await self._deployments.get_snapshot_for_user(
                tenant_id, user_id, existing.target_snapshot_id
            )
            if (
                existing.action != "rollback"
                or existing.agent_name != agent_name
                or existing.environment is not environment_name
                or target.snapshot_id != request.snapshot_id
            ):
                raise ConflictError("Deployment idempotency key was reused for another rollback")
            return await self.view(tenant_id, user_id, existing.deployment_id)
        environment = await self.environment(tenant_id, user_id, agent_name, environment_name)
        if environment.revision != request.expected_environment_revision:
            raise ConflictError("Environment revision changed before rollback")
        snapshot = await self._deployments.get_snapshot_for_user(
            tenant_id, user_id, request.snapshot_id
        )
        if (
            snapshot.agent_name != agent_name
            or snapshot.environment is not environment_name
            or not snapshot.eval_gate_passed
        ):
            raise ConflictError(
                "Rollback requires a historical verified Snapshot from this environment"
            )
        deployment = Deployment(
            tenantId=tenant_id,
            deploymentId=self._ids("deployment"),
            agentName=agent_name,
            environment=environment_name,
            action="rollback",
            targetSnapshotId=snapshot.snapshot_id,
            previousSnapshotId=environment.healthy_snapshot_id,
            canaryPercent=100,
            expectedEnvironmentRevision=environment.revision,
            idempotencyKey=request.idempotency_key,
            requestedBy=user_id,
            status=DeploymentStatus.QUEUED,
            createdAt=self._clock(),
            updatedAt=self._clock(),
        )
        await self._deployments.add(deployment)
        await self._queue.enqueue(
            DeploymentTask(tenant_id=tenant_id, deployment_id=deployment.deployment_id)
        )
        await self._record(deployment, user_id, "studio.deployment.rollback")
        return DeploymentView(deployment=deployment, target=snapshot, environment=environment)

    async def view(self, tenant_id: str, owner_user_id: str, deployment_id: str) -> DeploymentView:
        deployment = await self._deployments.get_for_user(tenant_id, owner_user_id, deployment_id)
        return DeploymentView(
            deployment=deployment,
            target=await self._deployments.get_snapshot_for_user(
                tenant_id, owner_user_id, deployment.target_snapshot_id
            ),
            environment=await self.environment(
                tenant_id,
                deployment.requested_by,
                deployment.agent_name,
                deployment.environment,
            ),
        )

    async def list(
        self, tenant_id: str, owner_user_id: str, agent_name: str
    ) -> list[DeploymentView]:
        return [
            await self.view(tenant_id, owner_user_id, item.deployment_id)
            for item in await self._deployments.list_for_agent(tenant_id, owner_user_id, agent_name)
        ]

    async def snapshots(
        self, tenant_id: str, owner_user_id: str, agent_name: str
    ) -> list[DeploymentSnapshot]:
        return await self._deployments.list_snapshots(tenant_id, owner_user_id, agent_name)

    async def resolve(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        environment_name: EnvironmentName,
        routing_key: str,
    ) -> DeploymentResolution:
        environment = await self._environments.get(
            tenant_id, owner_user_id, agent_name, environment_name
        )
        if not environment.routes:
            raise ConflictError(
                f"Environment has no active deployment: {agent_name}/{environment_name}"
            )
        bucket = int(hashlib.sha256(routing_key.encode()).hexdigest()[:8], 16) % 100
        cumulative = 0
        selected = environment.routes[-1]
        for route in environment.routes:
            cumulative += route.weight
            if bucket < cumulative:
                selected = route
                break
        snapshot = await self._deployments.get_snapshot_for_user(
            tenant_id, owner_user_id, selected.snapshot_id
        )
        catalog = await self._catalog(tenant_id)
        self._validate_policy_catalog(environment.resource_policy, catalog)
        await self._validate_knowledge_references(
            tenant_id,
            environment.resource_policy.allowed_knowledge_references,
        )
        version = await self._registry.get(
            tenant_id,
            snapshot.created_by,
            snapshot.agent_name,
            snapshot.agent_version,
        )
        self._validate_agent_policy(
            version,
            environment.resource_policy,
            execution_profile=snapshot.execution_profile,
            execution_profile_version=snapshot.execution_profile_version,
        )
        return DeploymentResolution(
            agentName=agent_name,
            agentVersion=snapshot.agent_version,
            environment=environment_name,
            snapshotId=snapshot.snapshot_id,
            environmentPolicySnapshot=self._policy_snapshot(
                environment,
                captured_at=self._clock(),
            ),
        )

    async def _same_promote(self, existing: Deployment, request: PromoteRequest) -> DeploymentView:
        target = await self._deployments.get_snapshot_for_user(
            existing.tenant_id,
            existing.requested_by,
            existing.target_snapshot_id,
        )
        if (
            existing.action != "promote"
            or target.agent_version != request.agent_version
            or existing.environment is not request.environment
            or existing.canary_percent != request.canary_percent
        ):
            raise ConflictError("Deployment idempotency key was reused for another promotion")
        return await self.view(existing.tenant_id, existing.requested_by, existing.deployment_id)

    async def _record(self, deployment: Deployment, user_id: str, action: str) -> None:
        if self._audit:
            await self._audit.record(
                tenant_id=deployment.tenant_id,
                user_id=user_id,
                action=action,
                resource_type="deployment",
                resource_id=deployment.deployment_id,
                outcome="success",
                details={
                    "agent_name": deployment.agent_name,
                    "environment": deployment.environment.value,
                    "target_snapshot_id": deployment.target_snapshot_id,
                    "expected_revision": deployment.expected_environment_revision,
                },
            )
