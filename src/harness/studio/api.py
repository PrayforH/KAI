"""Agent Studio API contract backed by trusted Harness identities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import Response

from harness.agent_package import (
    MAX_AGENT_BUNDLE_UPLOAD_BYTES,
    AgentBundleValidationError,
)
from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
)
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import RunStatus
from harness.deployments.controller import DeploymentController
from harness.deployments.models import (
    DeploymentSnapshot,
    DeploymentView,
    Environment,
    EnvironmentName,
    PromoteRequest,
    ReplaceEnvironmentPolicyRequest,
    RollbackRequest,
)
from harness.deployments.service import DeploymentService
from harness.evals.controller import EvalController
from harness.evals.models import (
    CreateEvalDatasetVersionRequest,
    ImportEvalDatasetRequest,
    CreateEvalRunRequest,
    EvalDatasetVersion,
    EvalGateResult,
    EvalRunView,
)
from harness.evals.service import EvalControlPlaneService
from harness.quality.models import (
    AlertIncident,
    AlertRule,
    CreateAlertRuleRequest,
    DatasetProjection,
    HumanFeedbackRequest,
    QualityGateResult,
    QualityScore,
)
from harness.quality.service import QualityService
from harness.quota.models import (
    QuotaPolicy,
    QuotaUsageView,
    ReplaceQuotaPolicyRequest,
)
from harness.quota.repositories import QuotaExceededError
from harness.quota.service import QuotaService
from harness.studio.agent_builder import (
    AgentBuilderPatch,
    AgentBuilderPatchRequest,
    CreateTaskDrivenDraftRequest,
    TaskDrivenDraftResult,
)
from harness.studio.bundle_import import AgentBundleImportError
from harness.studio.catalog_service import CapabilityCatalogService, CatalogResourceType
from harness.studio.compiler import DraftCompilationError
from harness.studio.mcp_credential_store import (
    ConfigureMcpCredentialRequest,
    McpCredentialService,
    McpCredentialStatus,
)
from harness.studio.mcp_discovery import McpDiscoveryError, McpDiscoveryService
from harness.studio.model_configuration import (
    BindAgentModelRequest,
    ConfigureModelRequest,
    GeneratedVideoReference,
    GenerateImageRequest,
    GenerateImageResult,
    GenerateVideoRequest,
    ModelConfigurationList,
    ModelConfigurationService,
    ModelConnectionTestResult,
    VideoGenerationJob,
)
from harness.studio.models import (
    AgentDraft,
    AgentDraftSummary,
    CapabilityCatalog,
    CapabilityCatalogRecord,
    CatalogImpact,
    CatalogMutationResult,
    CreateAgentDraftRequest,
    DraftValidationResult,
    ImportedAgentBundle,
    ImportedSkill,
    InstalledSkill,
    McpCapability,
    McpDiscoveryRequest,
    McpDiscoveryResult,
    PublishAgentDraftRequest,
    PublishedAgentVersion,
    ReplaceAgentDraftRequest,
    ReplaceCapabilityCatalogRequest,
    UpsertCatalogResourceRequest,
)
from harness.studio.preflight_models import PreflightEvent
from harness.studio.preview_controller import PreviewController
from harness.studio.preview_models import CreatePreviewRequest, PreviewDeployment
from harness.studio.preview_service import PreviewService
from harness.studio.service import (
    AgentStudioService,
    StudioPublicationConflictError,
    StudioPublisherNotConfiguredError,
    compact_draft_for_editor,
)
from harness.studio.skill_builder import (
    SkillConversationReply,
    SkillConversationRequest,
    SkillConversationService,
    SkillConversationUnavailableError,
    SkillConversationUpstreamError,
)
from harness.studio.skill_import import (
    MAX_SKILL_UPLOAD_BYTES,
    SkillImportError,
    import_skill,
)
from harness.studio.try_run import (
    CreateStudioTryRunRequest,
    SolidifiedAgentResult,
    SolidifyStudioTryRunRequest,
    StudioTryRunView,
    build_codex_loop,
    final_text,
)


@dataclass(frozen=True)
class StudioActor:
    tenant_id: str
    user_id: str


def _authorize_studio_actor(identity: Identity, permission: str) -> StudioActor:
    ensure_permission(identity, permission)
    return StudioActor(tenant_id=identity.tenant_id, user_id=identity.user_id)


def require_studio_reader(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:read")


def require_studio_writer(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:write")


def require_studio_publisher(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:publish")


def require_studio_previewer(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:preview")


def require_studio_deployer(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:deploy")


def require_studio_catalog_admin(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:catalog:write")


def require_studio_quota_admin(
    identity: Annotated[Identity, Depends(require_identity)],
) -> StudioActor:
    return _authorize_studio_actor(identity, "studio:quota:write")


def get_studio_service(request: Request) -> AgentStudioService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "studio", None)
    if not isinstance(service, AgentStudioService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "studio_not_configured",
                "message": "Agent Studio control plane is not configured",
            },
        )
    return service


def get_skill_conversation_service(request: Request) -> SkillConversationService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "skill_conversation", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "skill_conversation_not_configured",
                "message": "当前环境未连接真实模型，无法开始 Skill 对话创建",
            },
        )
    return service


def get_catalog_service(request: Request) -> CapabilityCatalogService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "capability_catalogs", None)
    if not isinstance(service, CapabilityCatalogService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "catalog_not_configured",
                "message": "Capability Catalog is not configured",
            },
        )
    return service


def get_mcp_discovery_service(request: Request) -> McpDiscoveryService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "mcp_discovery", None)
    if not isinstance(service, McpDiscoveryService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "mcp_discovery_not_configured",
                "message": "MCP discovery is not configured",
            },
        )
    return service


def get_mcp_credential_service(request: Request) -> McpCredentialService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "mcp_credentials", None)
    if not isinstance(service, McpCredentialService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "mcp_credentials_not_configured",
                "message": "MCP credential storage is not configured",
            },
        )
    return service


def get_model_configuration_service(request: Request) -> ModelConfigurationService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "model_configurations", None)
    if not isinstance(service, ModelConfigurationService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "model_configuration_not_configured",
                "message": "Model configuration control plane is not configured",
            },
        )
    return service


def get_preview_service(request: Request) -> PreviewService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "previews", None)
    if not isinstance(service, PreviewService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "preview_not_configured",
                "message": "Studio Preview control plane is not configured",
            },
        )
    return service


def get_preview_controller(request: Request) -> PreviewController:
    container = getattr(request.app.state, "container", None)
    controller = getattr(container, "preview_controller", None)
    if not isinstance(controller, PreviewController):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "preview_controller_not_configured",
                "message": "Studio Preview controller is not configured",
            },
        )
    return controller


def get_eval_service(request: Request) -> EvalControlPlaneService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "evals", None)
    if not isinstance(service, EvalControlPlaneService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "eval_control_plane_not_configured",
                "message": "Studio Eval control plane is not configured",
            },
        )
    return service


def get_eval_controller(request: Request) -> EvalController:
    container = getattr(request.app.state, "container", None)
    controller = getattr(container, "eval_controller", None)
    if not isinstance(controller, EvalController):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "eval_controller_not_configured",
                "message": "Studio Eval controller is not configured",
            },
        )
    return controller


def get_deployment_service(request: Request) -> DeploymentService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "deployments", None)
    if not isinstance(service, DeploymentService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "deployment_control_plane_not_configured",
                "message": "Studio Deployment control plane is not configured",
            },
        )
    return service


def get_deployment_controller(request: Request) -> DeploymentController:
    container = getattr(request.app.state, "container", None)
    controller = getattr(container, "deployment_controller", None)
    if not isinstance(controller, DeploymentController):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "deployment_controller_not_configured",
                "message": "Studio Deployment controller is not configured",
            },
        )
    return controller


def get_quality_service(request: Request) -> QualityService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "quality", None)
    if not isinstance(service, QualityService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "quality_not_configured",
                "message": "Quality control plane is not configured",
            },
        )
    return service


def get_quota_service(request: Request) -> QuotaService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "quotas", None)
    if not isinstance(service, QuotaService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "quota_not_configured",
                "message": "Quota control plane is not configured",
            },
        )
    return service


router = APIRouter(prefix="/v1/studio", tags=["agent-studio"])


@router.get("/quotas", response_model=QuotaUsageView)
async def get_quota_usage(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[QuotaService, Depends(get_quota_service)],
) -> QuotaUsageView:
    return await service.usage(actor.tenant_id)


@router.put("/quotas/{policy_id}", response_model=QuotaPolicy)
async def replace_quota_policy(
    policy_id: str,
    body: ReplaceQuotaPolicyRequest,
    actor: Annotated[StudioActor, Depends(require_studio_quota_admin)],
    service: Annotated[QuotaService, Depends(get_quota_service)],
) -> QuotaPolicy:
    return await service.replace_policy(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        policy_id=policy_id,
        request=body,
    )


@router.get("/agents/{agent_name}/quality/scores", response_model=list[QualityScore])
async def list_quality_scores(
    agent_name: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> list[QualityScore]:
    return await service.list_scores(actor.tenant_id, actor.user_id, agent_name)


@router.get("/agents/{agent_name}/quality/incidents", response_model=list[AlertIncident])
async def list_quality_incidents(
    agent_name: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> list[AlertIncident]:
    return await service.list_incidents(actor.tenant_id, actor.user_id, agent_name)


@router.get("/agents/{agent_name}/quality/rules", response_model=list[AlertRule])
async def list_quality_rules(
    agent_name: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> list[AlertRule]:
    return await service.list_rules(actor.tenant_id, actor.user_id, agent_name)


@router.get(
    "/agents/{agent_name}/versions/{agent_version}/quality-gate", response_model=QualityGateResult
)
async def get_quality_gate(
    agent_name: str,
    agent_version: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> QualityGateResult:
    return await service.gate(actor.tenant_id, actor.user_id, agent_name, agent_version)


@router.post("/quality/rules", response_model=AlertRule, status_code=status.HTTP_201_CREATED)
async def create_quality_rule(
    body: CreateAlertRuleRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> AlertRule:
    return await service.add_rule(
        AlertRule(
            tenantId=actor.tenant_id,
            ruleId=f"quality_rule_{uuid4().hex}",
            agentName=body.agent_name,
            scoreName=body.score_name,
            minimumValue=body.minimum_value,
            minimumSamples=body.minimum_samples,
            blocksPromotion=body.blocks_promotion,
            dashboardUrl=body.dashboard_url,
            createdBy=actor.user_id,
            createdAt=datetime.now(UTC),
        )
    )


@router.post(
    "/runs/{run_id}/feedback", response_model=QualityScore, status_code=status.HTTP_201_CREATED
)
async def create_human_feedback(
    run_id: str,
    body: HumanFeedbackRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[QualityService, Depends(get_quality_service)],
) -> QualityScore:
    return await service.human_feedback(
        tenant_id=actor.tenant_id, user_id=actor.user_id, run_id=run_id, request=body
    )


@router.post(
    "/eval-datasets/{dataset_id}/versions/{version}/quality-sync",
    response_model=DatasetProjection,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_quality_dataset(
    dataset_id: str,
    version: int,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    evals: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
    quality: Annotated[QualityService, Depends(get_quality_service)],
) -> DatasetProjection:
    return await quality.project_dataset(
        await evals.get_dataset(actor.tenant_id, actor.user_id, dataset_id, version)
    )


@router.get(
    "/agents/{agent_name}/environments",
    response_model=list[Environment],
)
async def list_deployment_environments(
    agent_name: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
) -> list[Environment]:
    return await service.list_environments(actor.tenant_id, actor.user_id, agent_name)


@router.put(
    "/agents/{agent_name}/environments/{environment}/policy",
    response_model=Environment,
)
async def replace_environment_policy(
    agent_name: str,
    environment: EnvironmentName,
    body: ReplaceEnvironmentPolicyRequest,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
) -> Environment:
    try:
        return await service.replace_environment_policy(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            agent_name=agent_name,
            environment_name=environment,
            request=body,
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.get(
    "/agents/{agent_name}/deployments",
    response_model=list[DeploymentView],
)
async def list_deployments(
    agent_name: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
) -> list[DeploymentView]:
    return await service.list(actor.tenant_id, actor.user_id, agent_name)


@router.get(
    "/agents/{agent_name}/deployment-snapshots",
    response_model=list[DeploymentSnapshot],
)
async def list_deployment_snapshots(
    agent_name: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
) -> list[DeploymentSnapshot]:
    return await service.snapshots(actor.tenant_id, actor.user_id, agent_name)


@router.get("/deployments/{deployment_id}", response_model=DeploymentView)
async def get_deployment(
    deployment_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
) -> DeploymentView:
    try:
        return await service.view(actor.tenant_id, actor.user_id, deployment_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.post(
    "/deployments/promote",
    response_model=DeploymentView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def promote_deployment(
    body: PromoteRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
    controller: Annotated[DeploymentController, Depends(get_deployment_controller)],
) -> DeploymentView:
    try:
        result = await service.promote(
            tenant_id=actor.tenant_id, user_id=actor.user_id, request=body
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    container = getattr(request.app.state, "container", None)
    if getattr(container, "auto_execute", False):
        background_tasks.add_task(
            controller.drain_locally,
            actor.tenant_id,
            result.deployment.deployment_id,
        )
    return result


@router.post(
    "/agents/{agent_name}/environments/{environment}/rollback",
    response_model=DeploymentView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_deployment(
    agent_name: str,
    environment: EnvironmentName,
    body: RollbackRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_deployer)],
    service: Annotated[DeploymentService, Depends(get_deployment_service)],
    controller: Annotated[DeploymentController, Depends(get_deployment_controller)],
) -> DeploymentView:
    try:
        result = await service.rollback(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            agent_name=agent_name,
            environment_name=environment,
            request=body,
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    container = getattr(request.app.state, "container", None)
    if getattr(container, "auto_execute", False):
        background_tasks.add_task(
            controller.drain_locally,
            actor.tenant_id,
            result.deployment.deployment_id,
        )
    return result


@router.post(
    "/eval-datasets",
    response_model=EvalDatasetVersion,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_dataset(
    body: CreateEvalDatasetVersionRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> EvalDatasetVersion:
    try:
        return await service.create_dataset_version(
            tenant_id=actor.tenant_id, user_id=actor.user_id, request=body
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "draft_not_ready", "message": str(error)},
        ) from error


@router.post(
    "/eval-datasets/import",
    response_model=EvalDatasetVersion,
    status_code=status.HTTP_201_CREATED,
)
async def import_eval_dataset(
    body: ImportEvalDatasetRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> EvalDatasetVersion:
    """Import an uploaded question bank as a new durable Dataset version."""
    try:
        return await service.import_dataset_version(
            tenant_id=actor.tenant_id, user_id=actor.user_id, request=body
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "draft_not_ready", "message": str(error)},
        ) from error


@router.get("/eval-datasets", response_model=list[EvalDatasetVersion])
async def list_eval_datasets(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> list[EvalDatasetVersion]:
    return await service.list_datasets(actor.tenant_id, actor.user_id)


@router.get(
    "/eval-datasets/{dataset_id}/versions/{version}",
    response_model=EvalDatasetVersion,
)
async def get_eval_dataset(
    dataset_id: str,
    version: int,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> EvalDatasetVersion:
    try:
        return await service.get_dataset(actor.tenant_id, actor.user_id, dataset_id, version)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.post("/eval-runs", response_model=EvalRunView, status_code=status.HTTP_202_ACCEPTED)
async def create_eval_run(
    body: CreateEvalRunRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_previewer)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
    controller: Annotated[EvalController, Depends(get_eval_controller)],
) -> EvalRunView:
    try:
        result = await service.create_run(
            tenant_id=actor.tenant_id, user_id=actor.user_id, request=body
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    container = getattr(request.app.state, "container", None)
    if getattr(container, "auto_execute", False):
        assert isinstance(container, ApiContainer)
        background_tasks.add_task(
            controller.drain_locally,
            actor.tenant_id,
            result.run.eval_run_id,
            run_queue=container.task_queue,
            executor=container.worker,
        )
    return result


@router.get("/eval-runs", response_model=list[EvalRunView])
async def list_eval_runs(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> list[EvalRunView]:
    return await service.list_runs(actor.tenant_id, actor.user_id)


@router.get("/eval-runs/{eval_run_id}", response_model=EvalRunView)
async def get_eval_run(
    eval_run_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> EvalRunView:
    try:
        return await service.get_run(actor.tenant_id, actor.user_id, eval_run_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.post("/eval-runs/{eval_run_id}/cancel", response_model=EvalRunView)
async def cancel_eval_run(
    eval_run_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_previewer)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
    controller: Annotated[EvalController, Depends(get_eval_controller)],
) -> EvalRunView:
    try:
        result = await service.cancel_run(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            eval_run_id=eval_run_id,
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    container = getattr(request.app.state, "container", None)
    if getattr(container, "auto_execute", False):
        assert isinstance(container, ApiContainer)
        background_tasks.add_task(
            controller.drain_locally,
            actor.tenant_id,
            eval_run_id,
            run_queue=container.task_queue,
            executor=container.worker,
        )
    return result


@router.get("/eval-runs/{eval_run_id}/artifacts/{artifact_id}")
async def download_eval_artifact(
    eval_run_id: str,
    artifact_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> Response:
    try:
        name, media_type, content = await service.download_artifact(
            actor.tenant_id, actor.user_id, eval_run_id, artifact_id
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get(
    "/evaluation-gates/{agent_name}/versions/{agent_version}",
    response_model=EvalGateResult,
)
async def get_eval_gate(
    agent_name: str,
    agent_version: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> EvalGateResult:
    return await service.gate(actor.tenant_id, actor.user_id, agent_name, agent_version)


@router.get("/catalog", response_model=CapabilityCatalogRecord)
async def get_catalog(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[CapabilityCatalogService, Depends(get_catalog_service)],
) -> CapabilityCatalogRecord:
    return await service.get_for_user(actor.tenant_id, actor.user_id)


@router.get("/models", response_model=ModelConfigurationList)
async def list_model_configurations(
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> ModelConfigurationList:
    """List administrator-only connection metadata; credentials are status-only."""

    return await service.list(actor.tenant_id)


@router.put("/models/{route_id}", response_model=ModelConfigurationList)
async def configure_model(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    body: ConfigureModelRequest,
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> ModelConfigurationList:
    return await service.configure(actor.tenant_id, actor.user_id, route_id, body)


@router.delete("/models/{route_id}", response_model=ModelConfigurationList)
async def disable_model(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> ModelConfigurationList:
    return await service.disable(actor.tenant_id, actor.user_id, route_id, expected_revision)


@router.delete("/models/{route_id}/permanent", response_model=ModelConfigurationList)
async def delete_model(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> ModelConfigurationList:
    return await service.delete(actor.tenant_id, actor.user_id, route_id, expected_revision)


@router.put("/models/agent-bindings/{agent_name}", response_model=ModelConfigurationList)
async def bind_agent_model(
    agent_name: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    body: BindAgentModelRequest,
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> ModelConfigurationList:
    return await service.bind_agent(actor.tenant_id, actor.user_id, agent_name, body)


@router.post("/models/{route_id}/test", response_model=ModelConnectionTestResult)
async def test_model_connection(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> ModelConnectionTestResult:
    return await service.test(actor.tenant_id, route_id)


@router.post("/models/{route_id}/images", response_model=GenerateImageResult)
async def generate_image(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    body: GenerateImageRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> GenerateImageResult:
    ensure_permission(identity, "tasks:write")
    return await service.generate_image(identity.tenant_id, route_id, body)


@router.post("/models/{route_id}/videos", response_model=VideoGenerationJob)
async def generate_video(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    body: GenerateVideoRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> VideoGenerationJob:
    ensure_permission(identity, "tasks:write")
    references: list[GeneratedVideoReference] = []
    if body.input_artifact_ids:
        artifacts = await container.input_artifacts.resolve_for_run(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            input_artifact_ids=body.input_artifact_ids,
        )
        for artifact in artifacts:
            if not artifact.media_type.lower().startswith("image/"):
                raise ConflictError("video references must be image files")
            _metadata, content = await container.input_artifacts.download(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                input_artifact_id=artifact.input_artifact_id,
            )
            references.append(
                GeneratedVideoReference(
                    name=artifact.name,
                    media_type=artifact.media_type,
                    content=content,
                )
            )
    return await service.create_video_job(
        identity.tenant_id,
        identity.user_id,
        route_id,
        body,
        references=tuple(references),
    )


@router.get("/models/{route_id}/videos/{job_id}", response_model=VideoGenerationJob)
async def get_video_generation_job(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    job_id: Annotated[str, Path(pattern=r"^video_job_[a-f0-9]{32}$")],
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> VideoGenerationJob:
    ensure_permission(identity, "tasks:write")
    return await service.get_video_job(identity.tenant_id, identity.user_id, route_id, job_id)


@router.delete("/models/{route_id}/videos/{job_id}", response_model=VideoGenerationJob)
async def cancel_video_generation_job(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    job_id: Annotated[str, Path(pattern=r"^video_job_[a-f0-9]{32}$")],
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> VideoGenerationJob:
    ensure_permission(identity, "tasks:write")
    return await service.cancel_video_job(identity.tenant_id, identity.user_id, route_id, job_id)


@router.get("/models/{route_id}/videos/{job_id}/content")
async def download_generated_video(
    route_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$")],
    job_id: Annotated[str, Path(pattern=r"^video_job_[a-f0-9]{32}$")],
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ModelConfigurationService, Depends(get_model_configuration_service)],
) -> Response:
    ensure_permission(identity, "tasks:write")
    result = await service.download_video(identity.tenant_id, identity.user_id, route_id, job_id)
    headers = {
        "Content-Disposition": f'inline; filename="{route_id}.mp4"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if result.request_id:
        headers["X-Request-Id"] = result.request_id
    if result.inference_time_seconds:
        headers["X-Inference-Time-S"] = result.inference_time_seconds
    return Response(content=result.content, media_type=result.media_type, headers=headers)


@router.post("/mcp/discover", response_model=McpDiscoveryResult)
async def discover_mcp(
    body: McpDiscoveryRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[McpDiscoveryService, Depends(get_mcp_discovery_service)],
) -> McpDiscoveryResult:
    try:
        return await service.discover(
            body,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
        )
    except McpDiscoveryError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.summary},
        ) from error


@router.get("/mcp/credentials", response_model=tuple[McpCredentialStatus, ...])
async def list_mcp_credentials(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[McpCredentialService, Depends(get_mcp_credential_service)],
) -> tuple[McpCredentialStatus, ...]:
    return await service.list(actor.tenant_id, actor.user_id)


@router.put("/mcp/{reference}/credentials", response_model=McpCredentialStatus)
async def configure_mcp_credential(
    reference: Annotated[str, Path(pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")],
    body: ConfigureMcpCredentialRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[McpCredentialService, Depends(get_mcp_credential_service)],
) -> McpCredentialStatus:
    return await service.configure(actor.tenant_id, actor.user_id, reference, body)


@router.delete("/mcp/{reference}/credentials", response_model=McpCredentialStatus)
async def delete_mcp_credential(
    reference: Annotated[str, Path(pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")],
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[McpCredentialService, Depends(get_mcp_credential_service)],
) -> McpCredentialStatus:
    await service.delete(actor.tenant_id, actor.user_id, reference)
    return McpCredentialStatus(reference=reference, configured=False)


@router.put("/catalog", response_model=CapabilityCatalogRecord)
async def replace_catalog(
    body: ReplaceCapabilityCatalogRequest,
    actor: Annotated[StudioActor, Depends(require_studio_catalog_admin)],
    service: Annotated[CapabilityCatalogService, Depends(get_catalog_service)],
) -> CapabilityCatalogRecord:
    return await service.replace(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        request=body,
    )


@router.get(
    "/catalog/{resource_type}/{resource_id}/impact",
    response_model=CatalogImpact,
)
async def catalog_impact(
    resource_type: CatalogResourceType,
    resource_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[CapabilityCatalogService, Depends(get_catalog_service)],
) -> CatalogImpact:
    return await service.impact(
        actor.tenant_id,
        actor.user_id,
        resource_type,
        resource_id,
    )


@router.delete(
    "/catalog/mcp/{resource_id}/permanent",
    response_model=CatalogMutationResult,
)
async def delete_personal_mcp_resource(
    resource_id: str,
    expected_revision: int,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[CapabilityCatalogService, Depends(get_catalog_service)],
    credentials: Annotated[McpCredentialService, Depends(get_mcp_credential_service)],
) -> CatalogMutationResult:
    actor = _authorize_studio_actor(identity, "studio:write")
    result = await service.delete_mcp(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        resource_id=resource_id,
        expected_revision=expected_revision,
    )
    await credentials.delete(actor.tenant_id, actor.user_id, resource_id)
    return result


@router.delete(
    "/catalog/{resource_type}/{resource_id}",
    response_model=CatalogMutationResult,
)
async def disable_catalog_resource(
    resource_type: CatalogResourceType,
    resource_id: str,
    expected_revision: int,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[CapabilityCatalogService, Depends(get_catalog_service)],
) -> CatalogMutationResult:
    actor = _authorize_studio_actor(
        identity,
        "studio:write" if resource_type == "mcp" else "studio:catalog:write",
    )
    return await service.disable(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        expected_revision=expected_revision,
    )


@router.put(
    "/catalog/{resource_type}/{resource_id}",
    response_model=CatalogMutationResult,
)
async def upsert_catalog_resource(
    resource_type: CatalogResourceType,
    resource_id: str,
    body: UpsertCatalogResourceRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[CapabilityCatalogService, Depends(get_catalog_service)],
    credentials: Annotated[McpCredentialService, Depends(get_mcp_credential_service)],
) -> CatalogMutationResult:
    actor = _authorize_studio_actor(
        identity,
        "studio:write" if resource_type == "mcp" else "studio:catalog:write",
    )
    if (
        resource_type == "mcp"
        and isinstance(body.resource, McpCapability)
        and body.resource.auth_mode != "none"
        and not await credentials.is_configured(
            actor.tenant_id,
            actor.user_id,
            resource_id,
        )
    ):
        raise ConflictError("Authenticated MCP registration requires the current user's credential")
    return await service.upsert(
        tenant_id=actor.tenant_id,
        user_id=actor.user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        request=body,
    )


def _translate_domain_error(error: Exception) -> HTTPException:
    if isinstance(error, NotFoundError):
        return HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": str(error)},
        )
    if isinstance(error, QuotaExceededError):
        return HTTPException(
            status_code=409,
            detail={"code": "quota_exceeded", "message": str(error)},
        )
    if isinstance(error, ConflictError):
        return HTTPException(
            status_code=409,
            detail={"code": "draft_conflict", "message": str(error)},
        )
    raise error


@router.get("/capabilities", response_model=CapabilityCatalog)
async def capabilities(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> CapabilityCatalog:
    return await service.capabilities(actor.tenant_id, actor.user_id)


@router.get("/drafts", response_model=list[AgentDraftSummary])
async def list_drafts(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
    space_id: Annotated[str | None, Query(alias="spaceId")] = None,
) -> list[AgentDraftSummary]:
    if space_id is not None:
        try:
            return await service.list_workspace_drafts(actor.tenant_id, actor.user_id, space_id)
        except (ConflictError, NotFoundError, PermissionDeniedError) as error:
            raise _translate_domain_error(error) from error
    return await service.list(actor.tenant_id, actor.user_id)


@router.post("/skills/conversation", response_model=SkillConversationReply)
async def continue_skill_conversation(
    body: SkillConversationRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[
        SkillConversationService,
        Depends(get_skill_conversation_service),
    ],
) -> SkillConversationReply:
    try:
        return await service.respond(actor.tenant_id, body)
    except SkillConversationUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "skill_model_route_unavailable", "message": str(error)},
        ) from error
    except SkillConversationUpstreamError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "skill_model_failed", "message": str(error)},
        ) from error


@router.post("/skills/import", response_model=ImportedSkill)
async def import_skill_file(
    request: Request,
    _actor: Annotated[StudioActor, Depends(require_studio_writer)],
    filename: str = "skill.zip",
) -> ImportedSkill:
    content = await _read_skill_upload(request)
    try:
        return import_skill(content, filename=filename)
    except SkillImportError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "skill_import_invalid", "message": str(error)},
        ) from error


async def _read_skill_upload(request: Request) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "text/markdown",
        "text/plain",
    }:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "skill_import_media_type_invalid",
                "message": "Skill 导入支持 ZIP 或 UTF-8 SKILL.md",
            },
        )
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_SKILL_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "skill_import_too_large",
                    "message": "Skill 上传文件不能超过 100 MiB",
                },
            )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_SKILL_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "skill_import_too_large",
                    "message": "Skill 上传文件不能超过 100 MiB",
                },
            )
    return bytes(content)


@router.post("/drafts/{draft_id}/skills/import", response_model=InstalledSkill)
async def install_skill_file(
    draft_id: str,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
    filename: str = "skill.zip",
    expected_revision: int = Query(alias="expectedRevision", ge=1),
) -> InstalledSkill:
    try:
        imported = import_skill(await _read_skill_upload(request), filename=filename)
        draft = await service.install_skill(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            draft_id=draft_id,
            expected_revision=expected_revision,
            imported=imported,
        )
    except SkillImportError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "skill_import_invalid", "message": str(error)},
        ) from error
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    return _installed_skill_response(draft, imported)


def _installed_skill_response(draft: AgentDraft, imported: ImportedSkill) -> InstalledSkill:
    compact = compact_draft_for_editor(draft)
    compact_skill = next(
        skill for skill in compact.spec.skills if skill.name == imported.skill.name
    )
    return InstalledSkill(
        draft=compact,
        skillName=compact_skill.name,
        sourceContentHash=imported.source_content_hash,
        riskLevel=imported.risk_level,
        findings=imported.findings,
        warnings=imported.warnings,
        fileCount=len(imported.skill.files),
        binaryFileCount=sum(file.content_base64 is not None for file in imported.skill.files),
    )


@router.post(
    "/previews",
    response_model=PreviewDeployment,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_preview(
    body: CreatePreviewRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_previewer)],
    service: Annotated[PreviewService, Depends(get_preview_service)],
    controller: Annotated[PreviewController, Depends(get_preview_controller)],
) -> PreviewDeployment:
    try:
        preview = await service.create(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            request=body,
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "draft_not_ready",
                "message": str(error),
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in error.issues],
            },
        ) from error
    container = getattr(request.app.state, "container", None)
    if getattr(container, "auto_execute", False):
        background_tasks.add_task(controller.process_once)
    return preview


@router.get("/previews", response_model=list[PreviewDeployment])
async def list_previews(
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> list[PreviewDeployment]:
    return await service.list(actor.tenant_id, actor.user_id)


@router.get("/previews/{preview_id}", response_model=PreviewDeployment)
async def get_preview(
    preview_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> PreviewDeployment:
    try:
        return await service.get(actor.tenant_id, actor.user_id, preview_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.get(
    "/previews/{preview_id}/events",
    response_model=list[PreflightEvent],
)
async def get_preview_events(
    preview_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[PreviewService, Depends(get_preview_service)],
) -> list[PreflightEvent]:
    try:
        preview = await service.get(actor.tenant_id, actor.user_id, preview_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    return list(preview.preflight_result.events) if preview.preflight_result else []


@router.post("/previews/{preview_id}/cancel", response_model=PreviewDeployment)
async def cancel_preview(
    preview_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_previewer)],
    service: Annotated[PreviewService, Depends(get_preview_service)],
    controller: Annotated[PreviewController, Depends(get_preview_controller)],
) -> PreviewDeployment:
    try:
        preview = await service.cancel(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            preview_id=preview_id,
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    container = getattr(request.app.state, "container", None)
    if getattr(container, "auto_execute", False):
        background_tasks.add_task(controller.process_once)
    return preview


@router.post("/drafts", response_model=AgentDraft, status_code=status.HTTP_201_CREATED)
async def create_draft(
    body: CreateAgentDraftRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> AgentDraft:
    try:
        if body.space_id is not None and body.agent_id is not None:
            draft = await service.create_workspace_draft(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                space_id=body.space_id,
                agent_id=body.agent_id,
                request=body,
            )
        else:
            draft = await service.create(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                request=body,
            )
        return compact_draft_for_editor(draft)
    except (ConflictError, NotFoundError, PermissionDeniedError) as error:
        raise _translate_domain_error(error) from error


@router.post(
    "/drafts/from-task",
    response_model=TaskDrivenDraftResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_task_driven_draft(
    body: CreateTaskDrivenDraftRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> TaskDrivenDraftResult:
    """Compile one business task into a complete, explainable Agent draft."""

    try:
        result = await service.create_from_task(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            request=body,
        )
        return result.model_copy(update={"draft": compact_draft_for_editor(result.draft)})
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "task_builder_unavailable", "message": str(error)},
        ) from error
    except (ConflictError, NotFoundError, PermissionDeniedError) as error:
        raise _translate_domain_error(error) from error


@router.post(
    "/drafts/import",
    response_model=ImportedAgentBundle,
    status_code=status.HTTP_201_CREATED,
)
async def import_draft_bundle(
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> ImportedAgentBundle:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/zip":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "bundle_import_media_type_invalid",
                "message": "Agent Bundle 导入必须使用 Content-Type application/zip",
            },
        )
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_AGENT_BUNDLE_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "bundle_import_too_large",
                    "message": (
                        f"Agent Bundle 超过最大上传大小 {MAX_AGENT_BUNDLE_UPLOAD_BYTES} bytes"
                    ),
                },
            )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_AGENT_BUNDLE_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "bundle_import_too_large",
                    "message": (
                        f"Agent Bundle 超过最大上传大小 {MAX_AGENT_BUNDLE_UPLOAD_BYTES} bytes"
                    ),
                },
            )
    try:
        imported = await service.import_bundle(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            content=bytes(content),
        )
        return imported.model_copy(update={"draft": compact_draft_for_editor(imported.draft)})
    except (AgentBundleValidationError, AgentBundleImportError) as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "bundle_import_invalid", "message": str(error)},
        ) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "bundle_import_incompatible",
                "message": str(error),
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in error.issues],
            },
        ) from error


def _draft_etag(draft: AgentDraft) -> str:
    return f'"rev-{draft.revision}"'


def _require_if_match(request: Request, draft: AgentDraft) -> None:
    """ETag optimistic lock: If-Match must match the current revision.

    The body-level expectedRevision CAS remains authoritative; If-Match adds
    HTTP-native preconditions for shared-draft editors.
    """
    if_match = request.headers.get("if-match")
    if if_match is None or if_match.strip() == "*":
        return
    if if_match.strip() != _draft_etag(draft):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "draft_revision_changed",
                "message": (
                    "Agent draft was updated by another editor; "
                    f"expected {if_match.strip()} actual {_draft_etag(draft)}"
                ),
            },
        )


@router.get("/drafts/{draft_id}", response_model=AgentDraft)
async def get_draft(
    draft_id: str,
    response: Response,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> AgentDraft:
    try:
        draft = compact_draft_for_editor(
            await service.get(actor.tenant_id, actor.user_id, draft_id)
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    response.headers["ETag"] = _draft_etag(draft)
    return draft


@router.put("/drafts/{draft_id}", response_model=AgentDraft)
async def replace_draft(
    draft_id: str,
    body: ReplaceAgentDraftRequest,
    request: Request,
    response: Response,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> AgentDraft:
    try:
        current = await service.get(actor.tenant_id, actor.user_id, draft_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    _require_if_match(request, current)
    try:
        draft = compact_draft_for_editor(
            await service.replace(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                draft_id=draft_id,
                request=body,
            )
        )
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    response.headers["ETag"] = _draft_etag(draft)
    return draft


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    draft_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
) -> Response:
    try:
        await service.delete(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            draft_id=draft_id,
            expected_revision=expected_revision,
        )
    except (ConflictError, NotFoundError, PermissionDeniedError) as error:
        raise _translate_domain_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{draft_id}/validate", response_model=DraftValidationResult)
async def validate_draft(
    draft_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> DraftValidationResult:
    try:
        return await service.validate(actor.tenant_id, actor.user_id, draft_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.post("/drafts/{draft_id}/builder-patch", response_model=AgentBuilderPatch)
async def create_agent_builder_patch(
    draft_id: str,
    body: AgentBuilderPatchRequest,
    actor: Annotated[StudioActor, Depends(require_studio_writer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> AgentBuilderPatch:
    """Generate a reviewable whole-Agent patch without mutating the draft."""

    try:
        return await service.build_agent_patch(actor.tenant_id, actor.user_id, draft_id, body)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


async def _studio_try_run_view(
    container: ApiContainer,
    actor: StudioActor,
    draft_id: str,
    draft_revision: int,
    run_id: str,
) -> StudioTryRunView:
    run = await container.runs.get(actor.tenant_id, run_id)
    session = await container.sessions.get(actor.tenant_id, run.session_id)
    prefix = f"preview-{draft_id}-{draft_revision}-"
    if (
        session.user_id != actor.user_id
        or session.environment != "preview"
        or not session.agent_version.startswith(prefix)
    ):
        raise NotFoundError(f"Studio Try Run not found: {run_id}")
    events = await container.observed_events.list_after(actor.tenant_id, run_id, 0)
    approvals = await container.approvals.list_for_runs(actor.tenant_id, [run_id])
    artifacts = await container.artifacts.list_for_run(actor.tenant_id, run_id)
    return StudioTryRunView(
        draftId=draft_id,
        draftRevision=draft_revision,
        run=run,
        events=tuple(events),
        approvals=tuple(approvals),
        artifacts=tuple(artifacts),
        finalText=final_text(events),
        loop=build_codex_loop(run, events),
    )


@router.post(
    "/drafts/{draft_id}/try-runs",
    response_model=StudioTryRunView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_studio_try_run(
    draft_id: str,
    body: CreateStudioTryRunRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_previewer)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> StudioTryRunView:
    """Compile and execute the current draft without publishing it."""

    container = getattr(request.app.state, "container", None)
    if not isinstance(container, ApiContainer):
        raise HTTPException(status_code=503, detail="Harness container is unavailable")
    try:
        draft = await service.get(actor.tenant_id, actor.user_id, draft_id)
        if draft.revision != body.expected_revision:
            raise ConflictError(
                f"draft revision changed: expected {body.expected_revision}, "
                f"actual {draft.revision}"
            )
        graph = await service.preview_graph(actor.tenant_id, actor.user_id, draft_id)
        compiled = graph.root
        preview_version = (
            f"preview-{draft_id}-{draft.revision}-{compiled.report.snapshot.content_hash[:12]}"
        )
        for dependency in graph.dependencies:
            await container.agents.register_preview_snapshot(
                actor.tenant_id,
                actor.user_id,
                dependency.compiled.report.snapshot,
                version=dependency.preview_version,
                package_hash=dependency.compiled.report.package_hash,
                agent_id=dependency.draft.agent_id,
            )
        await container.agents.register_preview_snapshot(
            actor.tenant_id,
            actor.user_id,
            compiled.report.snapshot,
            version=preview_version,
            package_hash=compiled.report.package_hash,
            agent_id=draft.agent_id,
        )
        session_key = hashlib.sha256(
            f"{actor.tenant_id}:{actor.user_id}:{draft_id}:{body.idempotency_key}".encode()
        ).hexdigest()[:32]
        session = await container.sessions.create(
            actor.tenant_id,
            actor.user_id,
            draft.spec.name,
            preview_version,
            session_id=f"studio_try_{session_key}",
            preview=True,
        )
        creation = await container.runs.create_with_result(
            actor.tenant_id,
            session.session_id,
            body.idempotency_key,
            input={"prompt": body.prompt},
        )
        if container.auto_execute and creation.created:
            background_tasks.add_task(
                container.worker.execute, actor.tenant_id, creation.run.run_id
            )
        return await _studio_try_run_view(
            container, actor, draft_id, draft.revision, creation.run.run_id
        )
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "draft_not_ready",
                "message": str(error),
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in error.issues],
            },
        ) from error
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.get(
    "/drafts/{draft_id}/try-runs/{run_id}",
    response_model=StudioTryRunView,
)
async def get_studio_try_run(
    draft_id: str,
    run_id: str,
    draft_revision: Annotated[int, Query(alias="draftRevision", ge=1)],
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_previewer)],
) -> StudioTryRunView:
    container = getattr(request.app.state, "container", None)
    if not isinstance(container, ApiContainer):
        raise HTTPException(status_code=503, detail="Harness container is unavailable")
    try:
        return await _studio_try_run_view(container, actor, draft_id, draft_revision, run_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.post(
    "/drafts/{draft_id}/solidify",
    response_model=SolidifiedAgentResult,
)
async def solidify_studio_try_run(
    draft_id: str,
    body: SolidifyStudioTryRunRequest,
    request: Request,
    actor: Annotated[StudioActor, Depends(require_studio_publisher)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
    eval_service: Annotated[EvalControlPlaneService, Depends(get_eval_service)],
) -> SolidifiedAgentResult:
    """Freeze one successful Try Run into a release plus required eval baseline."""

    container = getattr(request.app.state, "container", None)
    if not isinstance(container, ApiContainer):
        raise HTTPException(status_code=503, detail="Harness container is unavailable")
    try:
        view = await _studio_try_run_view(
            container,
            actor,
            draft_id,
            body.draft_revision,
            body.run_id,
        )
        if view.run.status is not RunStatus.SUCCEEDED:
            raise ConflictError("only a succeeded Studio Try Run can be solidified")
        current = await service.get(actor.tenant_id, actor.user_id, draft_id)
        first_publish = current.revision == body.expected_revision == body.draft_revision
        idempotent_retry = (
            current.revision == body.expected_revision + 1
            and body.expected_revision == body.draft_revision
            and current.published_version == current.spec.version
        )
        if not first_publish and not idempotent_retry:
            raise ConflictError(
                "draft changed after the verified Try Run; run it again before solidifying"
            )
        if first_publish:
            session = await container.sessions.get(actor.tenant_id, view.run.session_id)
            await service.publish_preview_dependencies(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                draft_id=draft_id,
                preview_version=session.agent_version,
            )
        datasets = await eval_service.list_datasets(actor.tenant_id, actor.user_id)
        matching = [
            item
            for item in datasets
            if item.source_draft_id == draft_id
            and item.source_draft_revision == body.draft_revision
            and item.required
        ]
        if matching:
            dataset = max(matching, key=lambda item: item.version)
        else:
            dataset = await eval_service.create_dataset_version(
                tenant_id=actor.tenant_id,
                user_id=actor.user_id,
                request=CreateEvalDatasetVersionRequest(
                    draftId=draft_id,
                    expectedRevision=body.draft_revision,
                    name=f"{current.spec.display_name} · 发布基线",
                    datasetId=f"release-{current.spec.name}",
                    required=True,
                ),
            )
        version = await service.publish(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            draft_id=draft_id,
            expected_revision=current.revision,
        )
        updated = compact_draft_for_editor(
            await service.get(actor.tenant_id, actor.user_id, draft_id)
        )
        published = PublishedAgentVersion.model_validate(
            version.model_dump(exclude={"snapshot", "owner_user_id"})
        )
        return SolidifiedAgentResult(
            draft=updated,
            version=published,
            dataset=dataset,
            loop=view.loop,
        )
    except StudioPublicationConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "version_conflict", "message": str(error)},
        ) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "draft_not_ready",
                "message": str(error),
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in error.issues],
            },
        ) from error
    except StudioPublisherNotConfiguredError as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "studio_publisher_unavailable", "message": str(error)},
        ) from error
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error


@router.get("/drafts/{draft_id}/bundle")
async def download_bundle(
    draft_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> Response:
    try:
        compiled = await service.bundle(actor.tenant_id, actor.user_id, draft_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "draft_not_ready",
                "message": str(error),
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in error.issues],
            },
        ) from error
    return Response(
        content=compiled.bundle,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{compiled.filename}"',
            "ETag": f'"{hashlib.sha256(compiled.bundle).hexdigest()}"',
            "X-Agent-Content-SHA256": compiled.report.snapshot.content_hash,
            "X-Agent-Package-SHA256": compiled.report.package_hash,
        },
    )


@router.get("/drafts/{draft_id}/nexau-bundle")
async def download_nexau_bundle(
    draft_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_reader)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
) -> Response:
    try:
        exported = await service.nexau_bundle(actor.tenant_id, actor.user_id, draft_id)
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    return Response(
        content=exported.content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "ETag": f'"{hashlib.sha256(exported.content).hexdigest()}"',
            "X-Agent-Export-Format": "nexau",
        },
    )


@router.post("/drafts/{draft_id}/publish", response_model=PublishedAgentVersion)
async def publish_draft(
    draft_id: str,
    actor: Annotated[StudioActor, Depends(require_studio_publisher)],
    service: Annotated[AgentStudioService, Depends(get_studio_service)],
    body: PublishAgentDraftRequest | None = None,
) -> PublishedAgentVersion:
    try:
        version = await service.publish(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            draft_id=draft_id,
            expected_revision=(body.expected_revision if body is not None else None),
        )
        return PublishedAgentVersion.model_validate(
            version.model_dump(exclude={"snapshot", "owner_user_id"})
        )
    except StudioPublicationConflictError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "version_conflict", "message": str(error)},
        ) from error
    except (ConflictError, NotFoundError) as error:
        raise _translate_domain_error(error) from error
    except DraftCompilationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "draft_not_ready",
                "message": str(error),
                "issues": [issue.model_dump(mode="json", by_alias=True) for issue in error.issues],
            },
        ) from error
    except StudioPublisherNotConfiguredError as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "studio_publisher_unavailable", "message": str(error)},
        ) from error
