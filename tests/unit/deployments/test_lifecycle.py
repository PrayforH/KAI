from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from harness.api.dependencies import ApiContainer, build_memory_container
from harness.config import Settings
from harness.core.errors import ConflictError
from harness.deployments.models import (
    CredentialScope,
    DeploymentSnapshot,
    DeploymentStatus,
    EnvironmentName,
    EnvironmentQuotaBoundary,
    PromoteRequest,
    ReplaceEnvironmentPolicyRequest,
    RollbackRequest,
)
from harness.knowledge.models import (
    CreateKnowledgeBaseRequest,
    CreateKnowledgeSourceRequest,
    FileKnowledgeConfig,
    ReplaceKnowledgeSourceRequest,
)
from harness.quota.models import QuotaResource, ReplaceQuotaPolicyRequest
from harness.quota.repositories import QuotaExceededError
from harness.studio.models import (
    AgentDraft,
    AgentTemplate,
    CapabilityRisk,
    CreateAgentDraftRequest,
    ExecutionProfileMetadata,
    NetworkAccess,
    ReplaceAgentDraftRequest,
    ReplaceCapabilityCatalogRequest,
)

TENANT = "tenant-a"
USER = "release-manager"
IMAGE = "sha256:" + "1" * 64


async def published_versions(
    container: ApiContainer, name: str = "deployment-agent"
) -> tuple[AgentDraft, str, str]:
    draft = await container.studio.create(
        tenant_id=TENANT,
        user_id=USER,
        request=CreateAgentDraftRequest(
            name=name,
            domain="deployment",
            displayName="部署 Agent",
            description="验证部署生命周期与会话版本固定。",
            template=AgentTemplate.ANALYST,
        ),
    )
    first = await container.studio.publish(tenant_id=TENANT, user_id=USER, draft_id=draft.draft_id)
    published_draft = await container.studio.get(TENANT, USER, draft.draft_id)
    updated = await container.studio.replace(
        tenant_id=TENANT,
        user_id=USER,
        draft_id=draft.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=published_draft.revision,
            spec=published_draft.spec.model_copy(update={"version": "0.2.0"}),
        ),
    )
    second = await container.studio.publish(tenant_id=TENANT, user_id=USER, draft_id=draft.draft_id)
    return updated, first.version, second.version


def promotion(
    *,
    agent_name: str,
    version: str,
    revision: int,
    key: str,
    canary: int = 100,
) -> PromoteRequest:
    return PromoteRequest(
        agentName=agent_name,
        agentVersion=version,
        environment=EnvironmentName.PRODUCTION,
        expectedEnvironmentRevision=revision,
        canaryPercent=canary,
        imageDigest=IMAGE,
        executionProfile="isolated-default",
        config={"LOG_LEVEL": "info"},
        idempotencyKey=key,
    )


async def promote_and_drain(container: ApiContainer, request: PromoteRequest) -> DeploymentSnapshot:
    view = await container.deployments.promote(tenant_id=TENANT, user_id=USER, request=request)
    result = await container.deployment_controller.drain_locally(
        TENANT, view.deployment.deployment_id
    )
    assert result.status is DeploymentStatus.SUCCEEDED
    return view.target


@pytest.mark.asyncio
async def test_canary_only_routes_new_sessions_and_rollback_restores_snapshot() -> None:
    container = build_memory_container()
    draft, first_version, second_version = await published_versions(container)
    first = await promote_and_drain(
        container,
        promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=0,
            key="first-release",
        ),
    )
    old_session = await container.sessions.create(
        TENANT,
        USER,
        draft.spec.name,
        None,
        environment=EnvironmentName.PRODUCTION,
    )

    await promote_and_drain(
        container,
        promotion(
            agent_name=draft.spec.name,
            version=second_version,
            revision=1,
            key="canary-release",
            canary=50,
        ),
    )
    assert old_session.agent_version == first_version
    assert old_session.deployment_snapshot_id == first.snapshot_id

    selected_key = ""
    for index in range(1000):
        candidate = f"new-session-{index}"
        resolution = await container.deployments.resolve(
            TENANT, USER, draft.spec.name, EnvironmentName.PRODUCTION, candidate
        )
        if resolution.agent_version == second_version:
            selected_key = candidate
            break
    assert selected_key
    canary_session = await container.sessions.create(
        TENANT,
        USER,
        draft.spec.name,
        None,
        session_id=selected_key,
        environment=EnvironmentName.PRODUCTION,
    )
    assert canary_session.agent_version == second_version

    rollback = await container.deployments.rollback(
        tenant_id=TENANT,
        user_id=USER,
        agent_name=draft.spec.name,
        environment_name=EnvironmentName.PRODUCTION,
        request=RollbackRequest(
            snapshotId=first.snapshot_id,
            expectedEnvironmentRevision=2,
            idempotencyKey="rollback-first",
        ),
    )
    rolled_back = await container.deployment_controller.drain_locally(
        TENANT, rollback.deployment.deployment_id
    )
    environment = await container.deployments.environment(
        TENANT, USER, draft.spec.name, EnvironmentName.PRODUCTION
    )

    assert rolled_back.status is DeploymentStatus.SUCCEEDED
    assert environment.healthy_snapshot_id == first.snapshot_id
    assert [(route.snapshot_id, route.weight) for route in environment.routes] == [
        (first.snapshot_id, 100)
    ]
    assert old_session.agent_version == first_version


@pytest.mark.asyncio
async def test_environment_policy_snapshot_is_immutable_per_session() -> None:
    container = build_memory_container()
    draft, first_version, _ = await published_versions(
        container,
        "environment-boundary-agent",
    )
    await promote_and_drain(
        container,
        promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=0,
            key="environment-policy-release",
        ),
    )
    old_session = await container.sessions.create(
        TENANT,
        "user-a",
        draft.spec.name,
        None,
        agent_owner_user_id=USER,
        environment=EnvironmentName.PRODUCTION,
    )
    current = await container.deployments.environment(
        TENANT,
        USER,
        draft.spec.name,
        EnvironmentName.PRODUCTION,
    )
    tightened = current.resource_policy.model_copy(
        update={
            "quota": EnvironmentQuotaBoundary(
                maxRunBudgetUsd=0.5,
                maxModelTokens=100_000,
                maxArtifactBytes=1_024,
            )
        }
    )
    updated = await container.deployments.replace_environment_policy(
        tenant_id=TENANT,
        user_id=USER,
        agent_name=draft.spec.name,
        environment_name=EnvironmentName.PRODUCTION,
        request=ReplaceEnvironmentPolicyRequest(
            expectedEnvironmentRevision=current.revision,
            policy=tightened,
        ),
    )
    new_session = await container.sessions.create(
        TENANT,
        "user-b",
        draft.spec.name,
        None,
        agent_owner_user_id=USER,
        environment=EnvironmentName.PRODUCTION,
    )

    assert updated.revision == current.revision + 1
    assert updated.policy_revision == current.policy_revision + 1
    assert updated.routes == current.routes
    assert old_session.environment_snapshot is not None
    assert new_session.environment_snapshot is not None
    assert old_session.environment_snapshot["policyRevision"] == current.policy_revision
    assert new_session.environment_snapshot["policyRevision"] == updated.policy_revision
    assert (
        old_session.environment_snapshot["policyHash"]
        != new_session.environment_snapshot["policyHash"]
    )


@pytest.mark.asyncio
async def test_environment_policy_denies_agent_resources_and_workload_scope() -> None:
    container = build_memory_container()
    draft, first_version, _ = await published_versions(
        container,
        "environment-deny-agent",
    )
    current = await container.deployments.environment(
        TENANT,
        USER,
        draft.spec.name,
        EnvironmentName.PRODUCTION,
    )
    denied_route_policy = current.resource_policy.model_copy(
        update={"allowed_model_routes": ("glm-5-2",)}
    )
    denied = await container.deployments.replace_environment_policy(
        tenant_id=TENANT,
        user_id=USER,
        agent_name=draft.spec.name,
        environment_name=EnvironmentName.PRODUCTION,
        request=ReplaceEnvironmentPolicyRequest(
            expectedEnvironmentRevision=current.revision,
            policy=denied_route_policy,
        ),
    )
    with pytest.raises(ConflictError, match="model routes"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=promotion(
                agent_name=draft.spec.name,
                version=first_version,
                revision=denied.revision,
                key="environment-route-denied",
            ),
        )

    restored = await container.deployments.replace_environment_policy(
        tenant_id=TENANT,
        user_id=USER,
        agent_name=draft.spec.name,
        environment_name=EnvironmentName.PRODUCTION,
        request=ReplaceEnvironmentPolicyRequest(
            expectedEnvironmentRevision=denied.revision,
            policy=current.resource_policy.model_copy(
                update={"credential_scopes": (CredentialScope.USER,)}
            ),
        ),
    )
    await promote_and_drain(
        container,
        promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=restored.revision,
            key="environment-user-only",
        ),
    )
    with pytest.raises(ConflictError, match="workload credentials"):
        await container.sessions.create(
            TENANT,
            "trigger:external",
            draft.spec.name,
            None,
            agent_owner_user_id=USER,
            environment=EnvironmentName.PRODUCTION,
        )


@pytest.mark.asyncio
async def test_environment_allows_only_registered_knowledge_and_sessions_pin_snapshot() -> None:
    container = build_memory_container()
    source, _ = await container.knowledge.create_source(
        TENANT,
        USER,
        CreateKnowledgeSourceRequest.model_validate(
            {
                "reference": "handbook",
                "displayName": "Handbook",
                "kind": "file",
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "leave",
                            "title": "Leave",
                            "content": "Policy revision one.",
                        }
                    ],
                },
            }
        ),
    )
    await container.knowledge.create_base(
        TENANT,
        USER,
        CreateKnowledgeBaseRequest(
            reference="company-policy",
            displayName="Company policy",
            sourceReferences=("handbook",),
        ),
    )
    draft = await container.studio.create(
        tenant_id=TENANT,
        user_id=USER,
        request=CreateAgentDraftRequest(
            name="knowledge-agent",
            domain="policy",
            displayName="Knowledge Agent",
            description="Answers from governed policy snapshots.",
            template=AgentTemplate.ANALYST,
        ),
    )
    updated = await container.studio.replace(
        tenant_id=TENANT,
        user_id=USER,
        draft_id=draft.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=draft.revision,
            spec=draft.spec.model_copy(update={"knowledge_references": ("company-policy",)}),
        ),
    )
    version = await container.studio.publish(
        tenant_id=TENANT,
        user_id=USER,
        draft_id=updated.draft_id,
    )
    environment = await container.deployments.environment(
        TENANT,
        USER,
        updated.spec.name,
        EnvironmentName.PRODUCTION,
    )
    with pytest.raises(ConflictError, match="Knowledge Bases"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=promotion(
                agent_name=updated.spec.name,
                version=version.version,
                revision=environment.revision,
                key="knowledge-denied",
            ),
        )

    allowed = await container.deployments.replace_environment_policy(
        tenant_id=TENANT,
        user_id=USER,
        agent_name=updated.spec.name,
        environment_name=EnvironmentName.PRODUCTION,
        request=ReplaceEnvironmentPolicyRequest(
            expectedEnvironmentRevision=environment.revision,
            policy=environment.resource_policy.model_copy(
                update={"allowed_knowledge_references": ("company-policy",)}
            ),
        ),
    )
    await promote_and_drain(
        container,
        promotion(
            agent_name=updated.spec.name,
            version=version.version,
            revision=allowed.revision,
            key="knowledge-allowed",
        ),
    )
    first_session = await container.sessions.create(
        TENANT,
        USER,
        updated.spec.name,
        None,
        agent_owner_user_id=USER,
        environment=EnvironmentName.PRODUCTION,
    )
    old_binding = first_session.knowledge_snapshot_bindings[0]
    assert old_binding["snapshotId"] == source.active_snapshot_id

    current = await container.knowledge.get_source(TENANT, "handbook")
    await container.knowledge.replace_source(
        TENANT,
        USER,
        "handbook",
        ReplaceKnowledgeSourceRequest(
            expectedRevision=current.revision,
            displayName=current.display_name,
            description=current.description,
            acl=current.acl,
            config=FileKnowledgeConfig.model_validate(
                {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "leave",
                            "title": "Leave",
                            "content": "Policy revision two.",
                        }
                    ],
                }
            ),
        ),
    )
    await container.knowledge.sync_source(TENANT, USER, "handbook")
    second_session = await container.sessions.create(
        TENANT,
        USER,
        updated.spec.name,
        None,
        agent_owner_user_id=USER,
        environment=EnvironmentName.PRODUCTION,
    )

    assert first_session.knowledge_snapshot_bindings == (old_binding,)
    assert second_session.knowledge_snapshot_bindings[0]["snapshotId"] != old_binding["snapshotId"]


@pytest.mark.asyncio
async def test_promotion_rejects_agent_built_from_a_stale_tool_catalog() -> None:
    container = build_memory_container()
    draft, first_version, _ = await published_versions(
        container,
        "stale-tool-catalog-agent",
    )
    published_catalog = await container.capability_catalogs.get(TENANT)
    updated_catalog = await container.capability_catalogs.replace(
        tenant_id=TENANT,
        user_id=USER,
        request=ReplaceCapabilityCatalogRequest(
            expectedRevision=published_catalog.revision,
            catalog=published_catalog.catalog,
        ),
    )
    environment = await container.deployments.environment(
        TENANT,
        USER,
        draft.spec.name,
        EnvironmentName.PRODUCTION,
    )

    assert updated_catalog.revision == published_catalog.revision + 1
    assert environment.resource_policy.capability_catalog_revision == updated_catalog.revision
    with pytest.raises(ConflictError, match="tool directory catalog revision"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=promotion(
                agent_name=draft.spec.name,
                version=first_version,
                revision=environment.revision,
                key="stale-tool-catalog-release",
            ),
        )


@pytest.mark.asyncio
async def test_failed_reconcile_preserves_last_healthy_environment() -> None:
    container = build_memory_container()
    draft, first_version, second_version = await published_versions(
        container, "failed-deployment-agent"
    )
    first = await promote_and_drain(
        container,
        promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=0,
            key="healthy-release",
        ),
    )
    pending = await container.deployments.promote(
        tenant_id=TENANT,
        user_id=USER,
        request=promotion(
            agent_name=draft.spec.name,
            version=second_version,
            revision=1,
            key="broken-release",
        ),
    )

    async def fail(_snapshot: DeploymentSnapshot) -> None:
        raise RuntimeError("registry password must never escape")

    controller = container.deployment_controller
    controller._deploy = fail  # pyright: ignore[reportPrivateUsage]
    result = await controller.drain_locally(TENANT, pending.deployment.deployment_id)
    environment = await container.deployments.environment(
        TENANT, USER, draft.spec.name, EnvironmentName.PRODUCTION
    )

    assert result.status is DeploymentStatus.FAILED
    assert result.error_code == "deployment_reconcile_failed"
    assert "password" not in result.model_dump_json()
    assert environment.revision == 1
    assert environment.healthy_snapshot_id == first.snapshot_id


@pytest.mark.asyncio
async def test_concurrent_promotions_use_environment_compare_and_set() -> None:
    container = build_memory_container()
    draft, first_version, second_version = await published_versions(
        container, "concurrent-deployment-agent"
    )
    first = await container.deployments.promote(
        tenant_id=TENANT,
        user_id=USER,
        request=promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=0,
            key="concurrent-first",
        ),
    )
    second = await container.deployments.promote(
        tenant_id=TENANT,
        user_id=USER,
        request=promotion(
            agent_name=draft.spec.name,
            version=second_version,
            revision=0,
            key="concurrent-second",
        ),
    )
    controller = container.deployment_controller
    await controller.reconcile(TENANT, first.deployment.deployment_id)
    await controller.reconcile(TENANT, second.deployment.deployment_id)
    outcomes = await asyncio.gather(
        controller.reconcile(TENANT, first.deployment.deployment_id),
        controller.reconcile(TENANT, second.deployment.deployment_id),
    )
    environment = await container.deployments.environment(
        TENANT, USER, draft.spec.name, EnvironmentName.PRODUCTION
    )

    assert sorted(item.status.value for item in outcomes) == ["failed", "succeeded"]
    assert environment.revision == 1
    assert len(environment.routes) == 1


@pytest.mark.asyncio
async def test_deployment_idempotency_and_secret_free_config_contract() -> None:
    container = build_memory_container()
    draft, first_version, _second_version = await published_versions(
        container, "idempotent-deployment-agent"
    )
    request = promotion(
        agent_name=draft.spec.name,
        version=first_version,
        revision=0,
        key="same-release",
    )
    first = await container.deployments.promote(tenant_id=TENANT, user_id=USER, request=request)
    repeated = await container.deployments.promote(tenant_id=TENANT, user_id=USER, request=request)

    assert repeated.deployment.deployment_id == first.deployment.deployment_id
    with pytest.raises(ConflictError):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=request.model_copy(update={"agent_version": "0.2.0"}),
        )
    with pytest.raises(ValidationError, match="secret-like"):
        PromoteRequest(
            agentName=draft.spec.name,
            agentVersion=first_version,
            environment=EnvironmentName.PRODUCTION,
            expectedEnvironmentRevision=0,
            imageDigest=IMAGE,
            executionProfile="isolated-default",
            config={"api_token": "not-allowed"},
            idempotencyKey="unsafe-release",
        )

    with pytest.raises(ValidationError, match="platform-managed"):
        PromoteRequest(
            agentName=draft.spec.name,
            agentVersion=first_version,
            environment=EnvironmentName.PRODUCTION,
            expectedEnvironmentRevision=0,
            imageDigest=IMAGE,
            executionProfile="isolated-default",
            config={"provider_url": "https://unsafe.example"},
            idempotencyKey="unsafe-provider",
        )


@pytest.mark.asyncio
async def test_deployment_promotion_quota_rejects_before_snapshot_is_created() -> None:
    container = build_memory_container(settings=Settings(quota_enforcement_enabled=True))
    draft, first_version, second_version = await published_versions(
        container, "quota-deployment-agent"
    )
    await container.quotas.replace_policy(
        tenant_id=TENANT,
        user_id=USER,
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            limits={QuotaResource.DEPLOYMENT_PROMOTIONS: 1},
        ),
    )
    await container.deployments.promote(
        tenant_id=TENANT,
        user_id=USER,
        request=promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=0,
            key="quota-release-one",
        ),
    )

    with pytest.raises(QuotaExceededError, match="deployment_promotions"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=promotion(
                agent_name=draft.spec.name,
                version=second_version,
                revision=0,
                key="quota-release-two",
            ),
        )

    snapshots = await container.deployments.snapshots(TENANT, USER, draft.spec.name)
    assert len(snapshots) == 1


@pytest.mark.asyncio
async def test_profile_revision_requires_environment_approval_and_local_is_rejected() -> None:
    container = build_memory_container()
    draft, first_version, second_version = await published_versions(
        container, "profile-deployment-agent"
    )
    current_version = 2

    async def resolve_profile(_tenant_id: str, profile_id: str) -> ExecutionProfileMetadata:
        return ExecutionProfileMetadata(
            profileId=profile_id,
            label="Managed profile",
            description="Platform-owned execution boundary.",
            sandboxProvider=("local" if profile_id == "local-dev" else "daytona"),
            networkAccess=(
                NetworkAccess.NONE,
                NetworkAccess.INTERNAL,
                NetworkAccess.EXTERNAL,
            ),
            risk=CapabilityRisk.LOW,
            version=current_version,
            networkPolicyId="registered-mcp-only",
            allowedMcpReferences=("tavily-readonly",),
            productionAllowed=profile_id != "local-dev",
        )

    container.deployments._execution_profile_resolver = resolve_profile  # pyright: ignore[reportPrivateUsage]
    first = await promote_and_drain(
        container,
        promotion(
            agent_name=draft.spec.name,
            version=first_version,
            revision=0,
            key="profile-v1",
        ),
    )
    current_version = 3
    with pytest.raises(ConflictError, match="outside the Environment policy"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=promotion(
                agent_name=draft.spec.name,
                version=second_version,
                revision=1,
                key="profile-v2",
            ),
        )

    assert first.execution_profile_version == 2

    unsafe = promotion(
        agent_name=draft.spec.name,
        version=second_version,
        revision=1,
        key="local-production",
    ).model_copy(update={"execution_profile": "local-dev"})
    with pytest.raises(ConflictError, match="not enabled for production"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=unsafe,
        )
