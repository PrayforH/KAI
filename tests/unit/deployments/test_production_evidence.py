"""Production promotion binds the exact preflight, package, profile and policy."""

from datetime import UTC, datetime

import pytest

from harness.api.dependencies import build_memory_container
from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot
from harness.deployments.models import EnvironmentName, PromoteRequest
from tests.unit.deployments.test_lifecycle import (
    IMAGE,
    TENANT,
    USER,
    PreviewLookup,
    production_proof,
    published_versions,
)


@pytest.mark.asyncio
async def test_production_requires_matching_live_evidence_and_pins_policy() -> None:
    container = build_memory_container()
    draft, version_name, _ = await published_versions(container, "evidence-agent")
    version = await container.deployments._registry.get(  # pyright: ignore[reportPrivateUsage]
        TENANT, USER, draft.spec.name, version_name
    )
    assert version.package_hash is not None
    manifest = AgentManifestSnapshot.model_validate(version.snapshot).manifest
    policy = await container.governance.resolve_runtime(TENANT, manifest.spec.permissions.policy)
    request = await production_proof(
        container,
        PromoteRequest(
            agentName=draft.spec.name,
            agentVersion=version_name,
            environment=EnvironmentName.PRODUCTION,
            expectedEnvironmentRevision=0,
            imageDigest=IMAGE,
            executionProfile="isolated-default",
            idempotencyKey="release-proof",
        ),
    )
    lookup = container.deployments._previews  # pyright: ignore[reportPrivateUsage]
    assert isinstance(lookup, PreviewLookup)
    assert request.preview_id is not None
    preview = lookup.previews[request.preview_id]
    result = preview.preflight_result
    assert result is not None
    now = result.completed_at
    profile_hash = result.execution_profile_hash
    with pytest.raises(ConflictError, match="verified Preview"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=request.model_copy(update={"preview_id": None}),
        )
    current_preview = preview

    lookup.previews[preview.preview_id] = current_preview
    promoted = await container.deployments.promote(
        tenant_id=TENANT,
        user_id=USER,
        request=request,
    )
    assert promoted.target.preflight_policy_hash == policy.content_hash
    assert promoted.target.preflight_completed_at == now
    assert promoted.target.preflight_result_hash is not None
    assert promoted.target.execution_profile_hash == profile_hash

    current_preview = preview.model_copy(
        update={
            "preflight_result": result.model_copy(
                update={"policy_hash": "0" * 64, "execution_profile_hash": "0" * 64},
            )
        }
    )
    lookup.previews[preview.preview_id] = current_preview
    with pytest.raises(ConflictError, match="evidence is missing or drifted"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=request.model_copy(update={"idempotency_key": "release-drift"}),
        )
    current_preview = preview.model_copy(
        update={
            "preflight_result": result.model_copy(
                update={"policy_hash": None},
            )
        }
    )
    lookup.previews[preview.preview_id] = current_preview
    with pytest.raises(ConflictError, match="evidence is missing or drifted"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=request.model_copy(update={"idempotency_key": "release-legacy"}),
        )
    current_preview = preview
    lookup.previews[preview.preview_id] = current_preview

    async def changed_policy(_tenant: str, _name: str):
        return policy.__class__(
            policy_id=policy.policy_id,
            revision=policy.revision,
            content_hash="0" * 64,
            call_policy=policy.call_policy,
            result_policy=policy.result_policy,
        )

    container.deployments._policy_resolver = changed_policy  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ConflictError, match="evidence is missing or drifted"):
        await container.deployments.promote(
            tenant_id=TENANT,
            user_id=USER,
            request=request.model_copy(update={"idempotency_key": "release-policy-drift"}),
        )


@pytest.mark.asyncio
async def test_historical_snapshot_without_preflight_fields_still_loads() -> None:
    from harness.deployments.models import DeploymentSnapshot

    payload = {
        "tenantId": TENANT,
        "snapshotId": "historical",
        "agentName": "evidence-agent",
        "agentVersion": "1",
        "environment": "production",
        "manifestHash": "a" * 64,
        "packageHash": "b" * 64,
        "imageDigest": IMAGE,
        "executionProfile": "isolated-default",
        "evalGatePassed": True,
        "evalRequiredDatasets": 0,
        "createdBy": USER,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    assert DeploymentSnapshot.model_validate(payload).preflight_policy_hash is None
