from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from harness.execution.credentials import CredentialResourceKind
from harness.governance.models import (
    CreateCredentialConnectionRequest,
    CreateGovernedPolicyRequest,
    CredentialConnection,
    GovernedPolicyProfile,
    PolicyImpactPreview,
    PolicyPublication,
    PolicySimulationResult,
    PreviewPolicyImpactRequest,
    PublishGovernedPolicyRequest,
    ReplaceCredentialConnectionRequest,
    ReplaceGovernedPolicyRequest,
    RevokeCredentialConnectionRequest,
    SimulateGovernedPolicyRequest,
)
from harness.governance.service import GovernanceService
from harness.sandbox.governance import SandboxGovernanceReport, SandboxGovernanceService
from harness.studio.api import (
    StudioActor,
    require_studio_deployer,
    require_studio_reader,
)

router = APIRouter(prefix="/v1/studio/governance", tags=["governance"])


def get_governance_service(request: Request) -> GovernanceService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "governance", None)
    if not isinstance(service, GovernanceService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "governance_not_configured",
                "message": "Governance control plane is not configured",
            },
        )
    return service


@router.get("/connections", response_model=list[CredentialConnection])
async def list_connections(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
    resource_kind: CredentialResourceKind | None = None,
    resource_reference: str | None = None,
) -> list[CredentialConnection]:
    return list(
        await service.list_connections(
            actor.tenant_id,
            resource_kind=resource_kind,
            resource_reference=resource_reference,
        )
    )


@router.post(
    "/connections",
    response_model=CredentialConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    body: CreateCredentialConnectionRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> CredentialConnection:
    return await service.create_connection(actor.tenant_id, actor.user_id, body)


@router.put(
    "/connections/{connection_id}",
    response_model=CredentialConnection,
)
async def replace_connection(
    connection_id: str,
    body: ReplaceCredentialConnectionRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> CredentialConnection:
    return await service.replace_connection(
        actor.tenant_id, actor.user_id, connection_id, body
    )


@router.post(
    "/connections/{connection_id}/revoke",
    response_model=CredentialConnection,
)
async def revoke_connection(
    connection_id: str,
    body: RevokeCredentialConnectionRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> CredentialConnection:
    return await service.revoke_connection(
        actor.tenant_id,
        actor.user_id,
        connection_id,
        expected_revision=body.expected_revision,
    )


@router.get("/policies", response_model=list[GovernedPolicyProfile])
async def list_policies(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> list[GovernedPolicyProfile]:
    return list(await service.list_policies(actor.tenant_id))


@router.post(
    "/policies",
    response_model=GovernedPolicyProfile,
    status_code=status.HTTP_201_CREATED,
)
async def create_policy(
    body: CreateGovernedPolicyRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> GovernedPolicyProfile:
    return await service.create_policy(actor.tenant_id, actor.user_id, body)


@router.get("/policies/{policy_id}", response_model=GovernedPolicyProfile)
async def get_policy(
    policy_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> GovernedPolicyProfile:
    return await service.get_policy(actor.tenant_id, policy_id)


@router.put("/policies/{policy_id}", response_model=GovernedPolicyProfile)
async def replace_policy(
    policy_id: str,
    body: ReplaceGovernedPolicyRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> GovernedPolicyProfile:
    return await service.replace_policy(
        actor.tenant_id, actor.user_id, policy_id, body
    )


@router.post(
    "/policies/{policy_id}/simulate",
    response_model=PolicySimulationResult,
)
async def simulate_policy(
    policy_id: str,
    body: SimulateGovernedPolicyRequest,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> PolicySimulationResult:
    return await service.simulate_draft(
        actor.tenant_id,
        policy_id,
        body.scenario,
    )


@router.post(
    "/policies/{policy_id}/impact",
    response_model=PolicyImpactPreview,
)
async def preview_policy_impact(
    policy_id: str,
    body: PreviewPolicyImpactRequest,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> PolicyImpactPreview:
    return await service.preview_impact(actor.tenant_id, policy_id, body)


@router.post(
    "/policies/{policy_id}/publish",
    response_model=PolicyPublication,
)
async def publish_policy(
    policy_id: str,
    body: PublishGovernedPolicyRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> PolicyPublication:
    return await service.publish_policy(
        actor.tenant_id,
        actor.user_id,
        policy_id,
        expected_revision=body.expected_revision,
    )


@router.get(
    "/policies/{policy_id}/publications",
    response_model=list[PolicyPublication],
)
async def list_policy_publications(
    policy_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[GovernanceService, Depends(get_governance_service)],
) -> list[PolicyPublication]:
    return list(await service.list_publications(actor.tenant_id, policy_id))


def get_sandbox_governance(request: Request) -> SandboxGovernanceService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "sandbox_governance", None)
    if not isinstance(service, SandboxGovernanceService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "sandbox_governance_not_configured",
                "message": "Sandbox instance governance is not configured",
            },
        )
    return service


@router.get("/sandbox-instances", response_model=SandboxGovernanceReport)
async def list_sandbox_instances(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[SandboxGovernanceService, Depends(get_sandbox_governance)],
) -> SandboxGovernanceReport:
    """Durable sandbox ownership for this tenant: leases against live instances."""

    return await service.report(tenant_id=actor.tenant_id)
