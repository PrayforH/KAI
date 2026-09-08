"""FastAPI dependencies and the local in-memory composition root."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast
from uuid import uuid4

from fastapi import Header, HTTPException, Request
from pydantic import SecretStr
from starlette.applications import Starlette

from harness.adapters.memory import (
    InMemoryAgentRegistry,
    InMemoryApprovalRepository,
    InMemoryArtifactRepository,
    InMemoryArtifactStore,
    InMemoryCancellationWakeup,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryInputArtifactRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryTaskQueue,
    InMemoryThreadFileRepository,
    InMemoryUserMemoryRepository,
    InMemoryWorkspaceSnapshotRepository,
)
from harness.agui.service import AguiRunService
from harness.application.agent_assets import stage_published_agent_assets
from harness.application.agents import AgentService
from harness.application.approvals import ApprovalService
from harness.application.artifacts import ArtifactService
from harness.application.events import EventService
from harness.application.file_catalog import FileCatalogService
from harness.application.input_artifacts import InputArtifactService
from harness.application.memory import UserMemoryService
from harness.application.runs import RunQuotaPlan, RunService
from harness.application.sessions import SessionService
from harness.application.workspaces import WorkspacePolicy, WorkspaceService
from harness.auth.api_access import ApiAccessService, InMemoryApiAccessKeyRepository
from harness.auth.audit import AuditService
from harness.auth.repositories import InMemoryAuditRepository, InMemoryAuthRepository
from harness.auth.service import (
    AuthenticationError,
    AuthService,
    OAuthProviderConfig,
)
from harness.config import Settings
from harness.context.repositories import InMemoryContextRepository
from harness.context.service import ContextService
from harness.core.errors import NotFoundError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import Run, RunStatus, Session
from harness.core.ports import EventRepository, EventWakeup, TaskQueue
from harness.deployments.controller import DeploymentController
from harness.deployments.queue import DeploymentTaskQueue
from harness.deployments.repositories import (
    DeploymentRepository,
    EnvironmentRepository,
    InMemoryDeploymentRepository,
    InMemoryEnvironmentRepository,
)
from harness.deployments.service import DeploymentService
from harness.evals.controller import EvalController
from harness.evals.queue import EvalTaskQueue
from harness.evals.repositories import (
    EvalDatasetRepository,
    EvalRunRepository,
    InMemoryEvalDatasetRepository,
    InMemoryEvalRunRepository,
)
from harness.evals.service import EvalControlPlaneService
from harness.governance.repositories import InMemoryGovernanceRepository
from harness.governance.service import GovernanceService
from harness.inputs.processors import DefaultInputProcessor
from harness.knowledge.directory import InMemoryUserDirectory
from harness.knowledge.repositories import InMemoryKnowledgeRepository
from harness.knowledge.service import KnowledgeService
from harness.knowledge.workload import (
    KnowledgeWorkloadTokenService,
    RemoteKnowledgeMcpProvider,
    build_knowledge_mcp_app,
)
from harness.lifecycle.adapters import EmptyLifecycleAdapter
from harness.lifecycle.controller import DataLifecycleController
from harness.lifecycle.models import LifecycleScope, LifecycleScopeKind
from harness.lifecycle.repositories import InMemoryDataLifecycleRepository
from harness.lifecycle.service import DataLifecycleService
from harness.memory_bank.configuration import embedding_client
from harness.memory_bank.repositories import InMemoryMemoryBankRepository
from harness.memory_bank.service import MemoryBankService
from harness.memory_bank.workload import (
    MemoryWorkloadTokenService,
    RemoteMemoryMcpProvider,
    build_memory_mcp_app,
)
from harness.observability.provider import Observability, build_observability
from harness.platform_mcp.workload import (
    PlatformMcpTokenService,
    build_platform_mcp_app,
)
from harness.policy.profiles import PolicyProfileRegistry, default_policy_profiles
from harness.policy.runtime import ResolvedPolicy
from harness.quality.controller import QualitySyncController
from harness.quality.langfuse import DisabledQualityExporter
from harness.quality.queue import QualityTaskQueue
from harness.quality.repositories import InMemoryQualityRepository, QualityRepository
from harness.quality.service import QualityService
from harness.quota.repositories import InMemoryQuotaRepository
from harness.quota.service import QuotaService
from harness.reliability.adapters import ObservedEventRepository
from harness.reliability.controller import MaintenanceReaper, ReliabilityController
from harness.reliability.metrics import ReliabilityMetrics
from harness.reliability.probes import CapacityProbe, QueueStats
from harness.reliability.repositories import InMemoryReliabilityRepository
from harness.reliability.service import ReliabilityService
from harness.runtime.base import AgentRuntime
from harness.runtime.cc_switch import load_cc_switch_claude_config
from harness.runtime.codex_tool_gate import CodexToolGate
from harness.runtime.default_tools import (
    default_tool_resolver,
    server_secret_credential_provider,
)
from harness.runtime.fake import FakeRuntime
from harness.runtime.installed import INSTALLED_AGENT_RUNTIMES
from harness.runtime.registry_codex_runtime import RegistryCodexRuntime, RegistryRuntimeRouter
from harness.runtime.registry_runtime import RegistryClaudeRuntime
from harness.runtime.sdk_tool_gate import SdkToolGate
from harness.sandbox.daytona import (
    DaytonaSandboxProvider,
    SdkDaytonaClient,
)
from harness.sandbox.deferred import DeferredToolSandboxProvider
from harness.sandbox.e2b import E2BSandboxProvider, SdkE2BClient
from harness.sandbox.kubernetes import KubectlKubernetesClient, KubernetesSandboxProvider
from harness.sandbox.local import LocalSandboxProvider
from harness.sharing.repositories import InMemoryTeamSpaceRepository
from harness.sharing.service import TeamSpaceService
from harness.sharing.workspace_repositories import (
    AgentIdentityService,
    InMemoryWorkspaceAgentRepository,
    WorkspaceAgentRepository,
)
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.mcp_credential_store import (
    InMemoryMcpCredentialRepository,
    McpCredentialCipher,
    McpCredentialService,
    StoredMcpCredentialProvider,
)
from harness.studio.mcp_discovery import (
    AutoDetectMcpConnector,
    McpDiscoveryService,
)
from harness.studio.model_configuration import ModelConfigurationService
from harness.studio.preflight import LivePreflightProvisioner, LivePreflightRunner
from harness.studio.preflight_probes import (
    AnthropicSandboxModelProbe,
    FakeMcpPreflightProbe,
    FakeModelPreflightProbe,
    StreamableHttpMcpProbe,
)
from harness.studio.preview_controller import PreviewController
from harness.studio.preview_queue import PreviewTaskQueue
from harness.studio.preview_repositories import (
    InMemoryPreviewRepository,
    PreviewRepository,
)
from harness.studio.preview_service import PreviewService
from harness.studio.repositories import (
    AgentDraftRepository,
    InMemoryAgentDraftRepository,
)
from harness.studio.service import AgentStudioService
from harness.studio.skill_builder import (
    AnthropicCompatibleSkillConversationService,
    SkillConversationService,
)
from harness.studio.web_configuration import WebConfigurationService
from harness.triggers.repositories import InMemoryAgentTriggerRepository
from harness.triggers.service import AgentTriggerService
from harness.worker.orchestrator import RunOrchestrator


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    roles: frozenset[str] = frozenset({"owner"})
    email: str = ""
    display_name: str = ""
    authentication_method: str = "service"
    permissions: frozenset[str] | None = None


@dataclass(frozen=True)
class ApiContainer:
    environment: str
    api_bearer_token: SecretStr
    auth: AuthService
    api_access: ApiAccessService
    audit: AuditService
    agent_drafts: AgentDraftRepository
    capability_catalogs: CapabilityCatalogService
    mcp_discovery: McpDiscoveryService
    mcp_credentials: McpCredentialService
    web_configurations: WebConfigurationService
    model_configurations: ModelConfigurationService
    studio: AgentStudioService
    preview_repository: PreviewRepository
    previews: PreviewService
    preview_controller: PreviewController
    eval_dataset_repository: EvalDatasetRepository
    eval_run_repository: EvalRunRepository
    evals: EvalControlPlaneService
    eval_controller: EvalController
    environment_repository: EnvironmentRepository
    deployment_repository: DeploymentRepository
    deployments: DeploymentService
    deployment_controller: DeploymentController
    quality_repository: QualityRepository
    quality: QualityService
    quality_controller: QualitySyncController
    quotas: QuotaService
    lifecycle: DataLifecycleService
    lifecycle_controller: DataLifecycleController
    reliability_metrics: ReliabilityMetrics
    reliability: ReliabilityService
    reliability_controller: ReliabilityController
    agents: AgentService
    team_spaces: TeamSpaceService
    workspace_agents: WorkspaceAgentRepository
    context: ContextService
    sessions: SessionService
    runs: RunService
    triggers: AgentTriggerService
    approvals: ApprovalService
    artifacts: ArtifactService
    input_artifacts: InputArtifactService
    file_catalog: FileCatalogService
    memory: UserMemoryService
    memory_bank: MemoryBankService
    memory_mcp_app: Starlette
    memory_workload_tokens: MemoryWorkloadTokenService
    knowledge: KnowledgeService
    governance: GovernanceService
    knowledge_mcp_app: Starlette
    knowledge_workload_tokens: KnowledgeWorkloadTokenService
    platform_mcp_app: Starlette
    platform_mcp_tokens: PlatformMcpTokenService
    events: EventRepository
    observed_events: EventRepository
    task_queue: TaskQueue
    observability: Observability
    runtime: AgentRuntime
    worker: RunOrchestrator
    agui: AguiRunService
    auto_execute: bool
    event_wakeup: EventWakeup | None = None
    skill_conversation: SkillConversationService | None = None
    sandbox_maintenance: Callable[[], Awaitable[object]] | None = None
    close: Callable[[], Awaitable[None]] | None = None


def build_memory_container(
    *,
    auto_execute: bool = False,
    settings: Settings | None = None,
    policy_profiles: PolicyProfileRegistry | None = None,
) -> ApiContainer:
    resolved_settings = settings or Settings()
    registry = InMemoryAgentRegistry()
    team_space_repository = InMemoryTeamSpaceRepository()
    workspace_agent_repository = InMemoryWorkspaceAgentRepository()
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    approvals = InMemoryApprovalRepository()
    artifact_repository = InMemoryArtifactRepository()
    input_artifact_repository = InMemoryInputArtifactRepository()
    memory_repository = InMemoryUserMemoryRepository()
    memory_bank_repository = InMemoryMemoryBankRepository()
    knowledge_repository = InMemoryKnowledgeRepository()
    governance_repository = InMemoryGovernanceRepository()
    thread_file_repository = InMemoryThreadFileRepository()
    workspace_snapshot_repository = InMemoryWorkspaceSnapshotRepository()
    artifact_store = InMemoryArtifactStore()
    raw_events = InMemoryEventRepository()
    bus = InMemoryEventBus()
    cancellation_wakeup = InMemoryCancellationWakeup()
    queue = InMemoryTaskQueue()
    observability = build_observability(resolved_settings)
    reliability_metrics = ReliabilityMetrics()
    observed_events = ObservedEventRepository(raw_events, reliability_metrics)
    auth = AuthService(
        InMemoryAuthRepository(),
        jwt_secret=resolved_settings.auth_jwt_secret,
        issuer=resolved_settings.auth_issuer,
        audience=resolved_settings.auth_audience,
        access_token_minutes=resolved_settings.auth_access_token_minutes,
        refresh_token_days=resolved_settings.auth_refresh_token_days,
        allow_registration=resolved_settings.auth_allow_registration,
        default_tenant_id=resolved_settings.auth_default_tenant_id,
        google=OAuthProviderConfig(
            resolved_settings.auth_google_client_id,
            resolved_settings.auth_google_client_secret,
        ),
        github=OAuthProviderConfig(
            resolved_settings.auth_github_client_id,
            resolved_settings.auth_github_client_secret,
        ),
    )
    audit = AuditService(InMemoryAuditRepository())
    api_access = ApiAccessService(InMemoryApiAccessKeyRepository(), audit=audit)
    active_policy_profiles = policy_profiles or default_policy_profiles()
    governance = GovernanceService(
        governance_repository,
        static_profiles=active_policy_profiles,
        audit=audit,
    )
    agent_drafts = InMemoryAgentDraftRepository()
    preview_repository = InMemoryPreviewRepository()
    preview_queue = PreviewTaskQueue.memory()
    eval_dataset_repository = InMemoryEvalDatasetRepository()
    eval_run_repository = InMemoryEvalRunRepository()
    eval_queue = EvalTaskQueue.memory()
    environment_repository = InMemoryEnvironmentRepository()
    deployment_repository = InMemoryDeploymentRepository()
    deployment_queue = DeploymentTaskQueue.memory()
    quality_repository = InMemoryQualityRepository()
    quality_queue = QualityTaskQueue.memory()
    capability_catalog_repository = InMemoryCapabilityCatalogRepository()

    def clock() -> datetime:
        return datetime.now(UTC)

    def id_generator(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    context_service = ContextService(
        InMemoryContextRepository(),
        clock=clock,
        id_generator=id_generator,
    )

    knowledge = KnowledgeService(
        knowledge_repository,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
        directory=InMemoryUserDirectory(),
    )
    quotas = QuotaService(
        InMemoryQuotaRepository(),
        audit=audit,
        clock=clock,
        id_generator=id_generator,
    )
    enforced_quotas = quotas if resolved_settings.quota_enforcement_enabled else None
    lifecycle_repository = InMemoryDataLifecycleRepository()
    lifecycle_adapters = tuple(
        EmptyLifecycleAdapter(name)
        for name in ("object-store", "sdk-session", "memory", "langfuse", "postgresql")
    )

    async def lifecycle_scopes(tenant_id: str, scope: LifecycleScope) -> tuple[LifecycleScope, ...]:
        if scope.kind is not LifecycleScopeKind.SESSION:
            return (scope,)
        session = await sessions.get(tenant_id, scope.subject_id)
        return (
            scope,
            LifecycleScope(kind=LifecycleScopeKind.USER, subjectId=session.user_id),
            LifecycleScope(kind=LifecycleScopeKind.AGENT, subjectId=session.agent_name),
        )

    lifecycle = DataLifecycleService(
        lifecycle_repository,
        lifecycle_adapters,
        export_store=artifact_store,
        scope_resolver=lifecycle_scopes,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
    )
    lifecycle_controller = DataLifecycleController(
        lifecycle_repository,
        lifecycle_adapters,
        artifact_store,
        scope_resolver=lifecycle_scopes,
        clock=clock,
    )

    agent_ids = AgentIdentityService(
        workspace_agent_repository,
        clock=clock,
        id_generator=id_generator,
    )
    agent_service = AgentService(
        registry,
        clock=clock,
        environment=resolved_settings.environment,
        agent_ids=agent_ids,
    )
    team_spaces = TeamSpaceService(
        team_space_repository,
        workspace_agent_repository,
        registry,
        drafts=agent_drafts,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
    )
    knowledge.configure_team_grant_checker(team_spaces.has_knowledge_access)
    capability_catalogs = CapabilityCatalogService(
        capability_catalog_repository,
        agent_drafts,
        clock=clock,
    )
    environment_mcp_credentials = server_secret_credential_provider(
        references_json=resolved_settings.mcp_secret_references_json,
        secrets_json=resolved_settings.mcp_server_secrets_json.get_secret_value(),
    )
    mcp_credential_service = McpCredentialService(
        InMemoryMcpCredentialRepository(),
        McpCredentialCipher(resolved_settings.auth_jwt_secret),
        audit=audit,
    )
    web_configurations = WebConfigurationService(mcp_credential_service,
        enabled=resolved_settings.web_tools_enabled, provider=resolved_settings.web_search_provider,
        api_key=resolved_settings.web_search_api_key.get_secret_value())
    model_configurations = ModelConfigurationService(
        capability_catalogs,
        mcp_credential_service,
        environment=resolved_settings.environment,
    )
    mcp_credentials = StoredMcpCredentialProvider(
        mcp_credential_service,
        environment_mcp_credentials,
    )
    mcp_discovery = McpDiscoveryService(
        credentials=mcp_credentials,
        connector=AutoDetectMcpConnector(
            proxy_url=resolved_settings.mcp_discovery_proxy_url.get_secret_value()
        ),
    )
    studio_service = AgentStudioService(
        agent_drafts,
        catalogs=capability_catalogs,
        publisher=agent_service,
        registry=registry,
        knowledge=knowledge,
        audit=audit,
        agent_ids=agent_ids,
        draft_permissions=team_spaces,
        clock=clock,
        id_generator=lambda: id_generator("draft"),
    )
    preview_service = PreviewService(
        repository=preview_repository,
        queue=preview_queue,
        studio=studio_service,
        audit=audit,
        clock=clock,
        id_generator=lambda: id_generator("preview"),
        quotas=enforced_quotas,
    )
    event_service = EventService(
        raw_events,
        bus,
        clock=clock,
        id_generator=id_generator,
        trace_context=observability,
    )

    async def run_quota_plan(
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> RunQuotaPlan:
        version = await registry.get(tenant_id, owner_user_id, agent_name, agent_version)
        limits = AgentManifestSnapshot.model_validate(version.snapshot).manifest.spec.limits
        return RunQuotaPlan(
            max_budget_usd=limits.max_budget_usd,
            max_model_tokens=limits.max_model_tokens,
            ttl_seconds=(
                limits.timeout_seconds + 300
                if limits.timeout_seconds is not None
                else resolved_settings.run_reservation_ttl_seconds
            ),
        )

    run_service = RunService(
        sessions,
        runs,
        queue,
        event_service,
        clock=clock,
        id_generator=id_generator,
        observability=observability,
        metrics=reliability_metrics,
        admission=enforced_quotas,
        quota_plan_resolver=run_quota_plan,
        cancellation_wakeup=cancellation_wakeup,
    )
    session_service = SessionService(
        registry,
        sessions,
        clock=clock,
        id_generator=id_generator,
        knowledge_binding_resolver=knowledge.resolve_bindings,
    )
    trigger_service = AgentTriggerService(
        InMemoryAgentTriggerRepository(),
        sessions=session_service,
        runs=run_service,
        registry=registry,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
    )
    approval_service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=event_service,
        clock=clock,
        id_generator=id_generator,
        queue=queue,
        observability=observability,
        metrics=reliability_metrics,
    )
    artifact_service = ArtifactService(
        runs=runs,
        repository=artifact_repository,
        store=artifact_store,
        id_generator=id_generator,
        max_file_bytes=resolved_settings.output_artifact_max_bytes,
        sessions=sessions,
        quotas=enforced_quotas,
    )
    file_catalog_service = FileCatalogService(
        thread_file_repository,
        clock=clock,
        id_generator=id_generator,
    )
    input_artifact_service = InputArtifactService(
        repository=input_artifact_repository,
        store=artifact_store,
        id_generator=id_generator,
        clock=clock,
        processor=DefaultInputProcessor(),
        file_catalog=file_catalog_service,
    )
    eval_service = EvalControlPlaneService(
        datasets=eval_dataset_repository,
        runs=eval_run_repository,
        queue=eval_queue,
        studio=studio_service,
        registry=registry,
        object_store=artifact_store,
        previews=preview_service,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
    )
    eval_controller = EvalController(
        datasets=eval_dataset_repository,
        repository=eval_run_repository,
        queue=eval_queue,
        sessions=session_service,
        runs=run_service,
        events=event_service,
        inputs=input_artifact_service,
        object_store=artifact_store,
        clock=clock,
    )
    quality_service = QualityService(
        repository=quality_repository,
        queue=quality_queue,
        runs=runs,
        sessions=sessions,
        events=raw_events,
        artifacts=artifact_repository,
        metrics=reliability_metrics,
        clock=clock,
    )
    quality_controller = QualitySyncController(
        repository=quality_repository,
        queue=quality_queue,
        exporter=DisabledQualityExporter(),
    )
    deployment_service = DeploymentService(
        environments=environment_repository,
        deployments=deployment_repository,
        queue=deployment_queue,
        registry=registry,
        evals=eval_service,
        previews=preview_service,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
        quality_gate=quality_service.require_promotion_allowed,
        capability_catalog_resolver=capability_catalogs.get,
        knowledge_reference_validator=knowledge.require_bases,
        quotas=enforced_quotas,
    )
    session_service.configure_deployment_resolver(deployment_service.resolve)
    trigger_service.configure_deployment_resolver(deployment_service.resolve)
    deployment_controller = DeploymentController(
        environments=environment_repository,
        deployments=deployment_repository,
        queue=deployment_queue,
        clock=clock,
    )
    platform_mcp_tokens = PlatformMcpTokenService(resolved_settings.auth_jwt_secret)
    platform_mcp_app = build_platform_mcp_app(
        agents=agent_service,
        deployments=deployment_service,
        quotas=quotas,
        governance=governance,
        tokens=platform_mcp_tokens,
    )
    memory_bank = MemoryBankService(
        memory_bank_repository,
        embedder=embedding_client(resolved_settings),
        semantic_threshold=resolved_settings.memory_semantic_threshold,
        audit=audit,
        clock=clock,
        id_generator=id_generator,
    )
    memory_tokens = MemoryWorkloadTokenService(resolved_settings.memory_workload_token_secret)
    memory_mcp_app = build_memory_mcp_app(memory_bank, memory_tokens)
    remote_memory_mcp = RemoteMemoryMcpProvider(
        resolved_settings.memory_mcp_public_url, memory_tokens
    )
    knowledge_tokens = KnowledgeWorkloadTokenService(
        resolved_settings.knowledge_workload_token_secret
    )
    knowledge_mcp_app = build_knowledge_mcp_app(knowledge, knowledge_tokens)
    remote_knowledge_mcp = RemoteKnowledgeMcpProvider(
        resolved_settings.knowledge_mcp_public_url,
        knowledge_tokens,
    )
    memory_service = UserMemoryService(memory_repository, clock=clock, memory_bank=memory_bank)
    workspace_service = WorkspaceService(
        artifact_store,
        snapshots=workspace_snapshot_repository,
        max_archive_bytes=resolved_settings.workspace_archive_max_bytes,
        max_archive_members=resolved_settings.workspace_archive_max_members,
        sessions=sessions,
        quotas=enforced_quotas,
    )

    async def workspace_policy_resolver(
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> WorkspacePolicy:
        version = await registry.get(tenant_id, owner_user_id, agent_name, agent_version)
        manifest = AgentManifestSnapshot.model_validate(version.snapshot).manifest
        return WorkspacePolicy(
            restore_session=manifest.spec.workspace.restore_session,
            archive_on_complete=manifest.spec.workspace.archive_on_complete,
        )

    async def stage_runtime_assets(
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
        workspace: Path,
        allow_validated_graph: bool,
    ) -> tuple[str, ...]:
        return await stage_published_agent_assets(
            registry,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            agent_name=agent_name,
            agent_version=agent_version,
            workspace=workspace,
            allow_validated_graph=allow_validated_graph,
        )

    async def resolve_policy(
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> ResolvedPolicy:
        version = await registry.get(tenant_id, owner_user_id, agent_name, agent_version)
        manifest = AgentManifestSnapshot.model_validate(version.snapshot).manifest
        return await governance.resolve_runtime(tenant_id, manifest.spec.permissions.policy)

    policy = active_policy_profiles.resolve("local-standard")
    sandbox_maintenance: Callable[[], Awaitable[object]] | None = None
    if resolved_settings.sandbox_provider == "daytona":
        daytona_api_key = resolved_settings.daytona_api_key.get_secret_value()
        if not daytona_api_key:
            raise ValueError("HARNESS_DAYTONA_API_KEY is required for Daytona")
        daytona = DaytonaSandboxProvider(
            client=SdkDaytonaClient.from_config(
                api_key=daytona_api_key,
                api_url=resolved_settings.daytona_api_url or None,
                target=resolved_settings.daytona_target or None,
            ),
            snapshot=resolved_settings.daytona_snapshot or None,
            remote_workspace_root=resolved_settings.daytona_remote_workspace_root,
            cli_version=resolved_settings.daytona_claude_cli_version,
            cli_path=resolved_settings.daytona_claude_cli_path,
            delete_on_destroy=resolved_settings.daytona_delete_on_destroy,
            auto_stop_interval_minutes=(resolved_settings.daytona_auto_stop_interval_minutes),
            auto_delete_interval_minutes=(resolved_settings.daytona_auto_delete_interval_minutes),
            session_reuse_enabled=resolved_settings.daytona_session_reuse_enabled,
            session_idle_timeout_seconds=(resolved_settings.daytona_session_idle_timeout_seconds),
            warm_pool_max_sessions=(resolved_settings.daytona_warm_pool_max_sessions),
            max_collect_bytes=resolved_settings.workspace_archive_max_bytes,
            max_collect_members=resolved_settings.workspace_archive_max_members,
        )
        sandbox = daytona
        sandbox_maintenance = daytona.reap_expired
    elif resolved_settings.sandbox_provider == "e2b":
        e2b_api_key = resolved_settings.e2b_api_key.get_secret_value()
        if not e2b_api_key:
            raise ValueError("HARNESS_E2B_API_KEY is required for E2B")
        sandbox = E2BSandboxProvider(
            client=SdkE2BClient(api_key=e2b_api_key),
            template=resolved_settings.e2b_template,
            timeout_seconds=resolved_settings.e2b_timeout_seconds,
            allow_internet_access=resolved_settings.e2b_allow_internet_access,
            remote_workspace_root=resolved_settings.e2b_remote_workspace_root,
            cli_version=resolved_settings.e2b_claude_cli_version,
            cli_path=resolved_settings.e2b_claude_cli_path,
            max_collect_bytes=resolved_settings.workspace_archive_max_bytes,
            max_collect_members=resolved_settings.workspace_archive_max_members,
        )
    elif resolved_settings.sandbox_provider == "kubernetes":
        try:
            selector_raw = json.loads(resolved_settings.kubernetes_egress_gateway_selector_json)
        except json.JSONDecodeError:
            raise ValueError(
                "HARNESS_KUBERNETES_EGRESS_GATEWAY_SELECTOR_JSON must be JSON"
            ) from None
        if not isinstance(selector_raw, dict):
            raise ValueError("Kubernetes egress gateway selector must map strings")
        selector_items = cast(dict[object, object], selector_raw)
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in selector_items.items()
        ):
            raise ValueError("Kubernetes egress gateway selector must map strings")
        if not resolved_settings.kubernetes_image:
            raise ValueError("HARNESS_KUBERNETES_IMAGE is required for Kubernetes")
        if not resolved_settings.kubernetes_egress_proxy_url:
            raise ValueError("HARNESS_KUBERNETES_EGRESS_PROXY_URL is required for Kubernetes")
        kubernetes = KubernetesSandboxProvider(
            client=KubectlKubernetesClient(
                namespace=resolved_settings.kubernetes_namespace,
                kubectl_path=resolved_settings.kubernetes_kubectl_path,
                kubeconfig=resolved_settings.kubernetes_kubeconfig or None,
                context=resolved_settings.kubernetes_context or None,
            ),
            namespace=resolved_settings.kubernetes_namespace,
            image=resolved_settings.kubernetes_image,
            runtime_class_name=resolved_settings.kubernetes_runtime_class_name,
            service_account_name=resolved_settings.kubernetes_service_account_name,
            remote_workspace=resolved_settings.kubernetes_remote_workspace,
            cli_version=resolved_settings.kubernetes_claude_cli_version,
            cli_path=resolved_settings.kubernetes_claude_cli_path,
            ttl_seconds=resolved_settings.kubernetes_pod_ttl_seconds,
            ready_timeout_seconds=resolved_settings.kubernetes_ready_timeout_seconds,
            cpu_millis=resolved_settings.kubernetes_cpu_millis,
            memory_mib=resolved_settings.kubernetes_memory_mib,
            disk_mib=resolved_settings.kubernetes_disk_mib,
            egress_gateway_namespace=(resolved_settings.kubernetes_egress_gateway_namespace),
            egress_gateway_selector=cast(dict[str, str], selector_items),
            egress_gateway_port=resolved_settings.kubernetes_egress_gateway_port,
            egress_proxy_url=resolved_settings.kubernetes_egress_proxy_url,
            dns_namespace=resolved_settings.kubernetes_dns_namespace,
            max_collect_bytes=resolved_settings.workspace_archive_max_bytes,
            max_collect_members=resolved_settings.workspace_archive_max_members,
        )
        sandbox = kubernetes
        sandbox_maintenance = kubernetes.reap_expired
    else:
        sandbox = LocalSandboxProvider()
    preflight_sandbox = sandbox
    if (
        resolved_settings.runtime in {"claude-sdk", "multi"}
        and resolved_settings.sandbox_execution_mode == "worker_cli_deferred"
    ):
        if resolved_settings.sandbox_provider == "local":
            raise ValueError(
                "HARNESS_SANDBOX_EXECUTION_MODE=worker_cli_deferred requires "
                "Daytona, E2B, or Kubernetes"
            )
        sandbox = DeferredToolSandboxProvider(
            preflight_sandbox,
            provider_name=resolved_settings.sandbox_provider,
            max_active_runs=resolved_settings.worker_deferred_max_active_runs,
        )
    skill_conversation: SkillConversationService | None = None
    if resolved_settings.runtime == "fake":
        runtime: AgentRuntime = FakeRuntime()
        model_probe = FakeModelPreflightProbe()
        mcp_probe = FakeMcpPreflightProbe()
    else:
        credential_provider = mcp_credentials
        gateway = load_cc_switch_claude_config(resolved_settings.cc_switch_settings_path)
        skill_conversation = AnthropicCompatibleSkillConversationService((gateway,))
        tool_resolver = default_tool_resolver(
            credential_provider,
            catalogs=capability_catalogs,
            web_configurations=web_configurations,
        )
        claude_runtime = RegistryClaudeRuntime(
            registry=registry,
            config=gateway,
            model_configurations=model_configurations,
            tool_resolver=tool_resolver,
            tool_gate=SdkToolGate(
                profiles=active_policy_profiles,
                approvals=approval_service,
                events=event_service,
                context_service=context_service,
                quotas=enforced_quotas,
                observability=observability,
            ),
            memory_service=memory_service,
            memory_bank=memory_bank,
            remote_memory_mcp=remote_memory_mcp,
            knowledge=knowledge,
            remote_knowledge_mcp=remote_knowledge_mcp,
            observability=observability,
        )
        runtime = (
            RegistryRuntimeRouter(
                registry=registry,
                runtimes=dict(
                    zip(
                        INSTALLED_AGENT_RUNTIMES,
                        (
                            claude_runtime,
                            RegistryCodexRuntime(
                                registry=registry,
                                remote_memory_mcp=remote_memory_mcp,
                                codex_path=Path(resolved_settings.codex_cli_path),
                                model_configurations=model_configurations,
                                tool_resolver=tool_resolver,
                                model_by_route=resolved_settings.codex_model_by_route,
                                provider_by_route=resolved_settings.codex_provider_by_route,
                                approval_policy=resolved_settings.codex_approval_policy,
                                network_access=resolved_settings.codex_network_access,
                                tool_output_token_limit=(resolved_settings.codex_tool_output_token_limit),
                                server_request_handler=CodexToolGate(
                                    approvals=approval_service,
                                    events=event_service,
                                ).authorize,
                            ),
                        ),
                        strict=False,
                    )
                ),
            )
            if resolved_settings.runtime == "multi"
            else claude_runtime
        )
        model_probe = AnthropicSandboxModelProbe(gateway)
        mcp_probe = StreamableHttpMcpProbe(tool_resolver)
    preflight_runner = LivePreflightRunner(
        studio=studio_service,
        sandbox=preflight_sandbox,
        model_probe=model_probe,
        mcp_probe=mcp_probe,
        policies=active_policy_profiles,
        policy_resolver=governance.resolve_runtime,
        observability=observability,
        timeout_seconds=resolved_settings.preflight_timeout_seconds,
        clock=clock,
    )
    preview_controller = PreviewController(
        repository=preview_repository,
        queue=preview_queue,
        provisioner=LivePreflightProvisioner(
            runner=preflight_runner,
            repository=preview_repository,
            clock=clock,
        ),
        heartbeat_seconds=resolved_settings.worker_task_heartbeat_seconds,
        clock=clock,
        quotas=enforced_quotas,
    )
    worker = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=event_service,
        runtime=runtime,
        sandbox=sandbox,
        clock=clock,
        policy=policy,
        approvals=approval_service,
        workspaces=workspace_service,
        observability=observability,
        artifacts=artifact_service,
        input_artifacts=input_artifact_service,
        memory=memory_service,
        workspace_policy_resolver=workspace_policy_resolver,
        runtime_asset_stager=(
            stage_runtime_assets if resolved_settings.runtime != "fake" else None
        ),
        policy_resolver=resolve_policy,
        output_artifact_max_bytes=resolved_settings.output_artifact_max_bytes,
        quality_hook=quality_service.record_terminal_run,
        quotas=enforced_quotas,
        quota_plan_resolver=run_quota_plan,
        metrics=reliability_metrics,
        cancellation_wakeup=cancellation_wakeup,
        context_service=context_service,
    )
    agui = AguiRunService(
        sessions=session_service,
        runs=run_service,
        input_artifacts=input_artifact_service,
        contexts=context_service,
        knowledge_bindings=knowledge.resolve_bindings,
    )

    async def lifecycle_reap() -> int:
        await lifecycle.enqueue_due_retention_jobs()
        return int(await lifecycle_controller.process_once() is not None)

    reliability_repository = InMemoryReliabilityRepository()
    capacity_probe = CapacityProbe(
        runs=runs,
        approvals=approvals,
        previews=preview_repository,
        queue=cast(QueueStats, queue),
        lifecycle=lifecycle_repository,
        credentials=None,
    )
    reliability = ReliabilityService(
        reliability_repository,
        reliability_metrics,
        capacity_probe,
        clock=clock,
        id_generator=id_generator,
    )
    maintenance = [
        MaintenanceReaper("approval-expiry", "approval", approval_service.reap_expired),
        MaintenanceReaper("preview-expiry", "preview", preview_controller.reap_expired),
        MaintenanceReaper("quota-reservation", "quota", quotas.reap_expired_all),
        MaintenanceReaper("workspace-retention", "workspace", lifecycle_reap),
        MaintenanceReaper("memory-expiry", "memory", memory_bank.reap_expired),
        MaintenanceReaper("memory-index", "memory", memory_bank.reindex_pending),
    ]
    if sandbox_maintenance is not None:
        maintenance.append(MaintenanceReaper("sandbox-expiry", "sandbox", sandbox_maintenance))
    reliability_controller = ReliabilityController(
        runs=runs,
        events=event_service,
        repository=reliability_repository,
        metrics=reliability_metrics,
        thresholds={
            RunStatus.QUEUED: resolved_settings.stuck_queued_seconds,
            RunStatus.PROVISIONING: resolved_settings.stuck_provisioning_seconds,
            RunStatus.RUNNING: resolved_settings.stuck_running_seconds,
            RunStatus.WAITING_APPROVAL: resolved_settings.stuck_waiting_approval_seconds,
            RunStatus.CANCELLING: resolved_settings.stuck_cancelling_seconds,
        },
        maintenance=maintenance,
        quotas=enforced_quotas,
        clock=clock,
        id_generator=id_generator,
    )
    return ApiContainer(
        environment=resolved_settings.environment,
        api_bearer_token=resolved_settings.api_bearer_token,
        auth=auth,
        api_access=api_access,
        audit=audit,
        agent_drafts=agent_drafts,
        capability_catalogs=capability_catalogs,
        mcp_discovery=mcp_discovery,
        mcp_credentials=mcp_credential_service,
        web_configurations=web_configurations,
        model_configurations=model_configurations,
        studio=studio_service,
        preview_repository=preview_repository,
        previews=preview_service,
        preview_controller=preview_controller,
        eval_dataset_repository=eval_dataset_repository,
        eval_run_repository=eval_run_repository,
        evals=eval_service,
        eval_controller=eval_controller,
        environment_repository=environment_repository,
        deployment_repository=deployment_repository,
        deployments=deployment_service,
        deployment_controller=deployment_controller,
        quality_repository=quality_repository,
        quality=quality_service,
        quality_controller=quality_controller,
        quotas=quotas,
        lifecycle=lifecycle,
        lifecycle_controller=lifecycle_controller,
        reliability_metrics=reliability_metrics,
        reliability=reliability,
        reliability_controller=reliability_controller,
        agents=agent_service,
        team_spaces=team_spaces,
        workspace_agents=workspace_agent_repository,
        context=context_service,
        sessions=session_service,
        runs=run_service,
        triggers=trigger_service,
        approvals=approval_service,
        artifacts=artifact_service,
        input_artifacts=input_artifact_service,
        file_catalog=file_catalog_service,
        memory=memory_service,
        memory_bank=memory_bank,
        memory_mcp_app=memory_mcp_app,
        memory_workload_tokens=memory_tokens,
        knowledge=knowledge,
        governance=governance,
        knowledge_mcp_app=knowledge_mcp_app,
        knowledge_workload_tokens=knowledge_tokens,
        platform_mcp_app=platform_mcp_app,
        platform_mcp_tokens=platform_mcp_tokens,
        events=raw_events,
        observed_events=observed_events,
        task_queue=queue,
        observability=observability,
        runtime=runtime,
        worker=worker,
        agui=agui,
        auto_execute=auto_execute,
        event_wakeup=bus,
        skill_conversation=skill_conversation,
        sandbox_maintenance=sandbox_maintenance,
    )


def get_container(request: Request) -> ApiContainer:
    return request.app.state.container


async def require_identity(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
) -> Identity:
    container: ApiContainer = request.app.state.container
    api_key_identity = getattr(request.state, "api_key_identity", None)
    if isinstance(api_key_identity, Identity):
        request.state.identity = api_key_identity
        return api_key_identity
    scheme, separator, credential = (authorization or "").partition(" ")
    service_authenticated = bool(getattr(request.state, "service_authenticated", False))
    if separator and scheme.lower() == "bearer" and credential.count(".") == 2:
        try:
            claims = container.auth.authenticate_access_token(credential)
            user, membership = await container.auth.current_user(claims)
        except AuthenticationError as error:
            raise HTTPException(
                status_code=401,
                detail={"code": error.code, "message": str(error)},
                headers={
                    "WWW-Authenticate": "Bearer",
                    "X-Harness-Auth-Error": error.code,
                },
            ) from error
        identity = Identity(
            tenant_id=membership.tenant_id,
            user_id=user.user_id,
            roles=frozenset({membership.role}),
            email=user.email,
            display_name=user.display_name,
            authentication_method="jwt",
        )
        request.state.identity = identity
        return identity
    legacy_allowed = service_authenticated or container.environment != "production"
    if not legacy_allowed or not tenant_id or not user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "identity_required",
                "message": "Sign in with a valid access token",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    identity = Identity(tenant_id=tenant_id, user_id=user_id)
    request.state.identity = identity
    return identity


_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({"*"}),
    "admin": frozenset(
        {
            "agents:publish",
            "tasks:read",
            "tasks:write",
            "audit:read",
            "studio:read",
            "studio:write",
            "studio:preview",
            "studio:publish",
            "studio:deploy",
            "studio:triggers:write",
            "studio:catalog:write",
            "studio:quota:write",
            "data:lifecycle:admin",
            "data:lifecycle:self",
            "operations:read",
            "operations:admin",
            "members:read",
            "members:write",
        }
    ),
    "member": frozenset(
        {
            "tasks:read",
            "tasks:write",
            "studio:read",
            "studio:write",
            "studio:preview",
            "studio:publish",
            "data:lifecycle:self",
            "operations:read",
        }
    ),
    "viewer": frozenset({"tasks:read", "studio:read", "data:lifecycle:self", "operations:read"}),
}


def ensure_permission(identity: Identity, permission: str) -> None:
    if identity.permissions is not None:
        if permission not in identity.permissions:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "permission_denied",
                    "message": f"API key permission required: {permission}",
                },
            )
        return
    granted: set[str] = set()
    for role in identity.roles:
        granted.update(_ROLE_PERMISSIONS.get(role, frozenset()))
    if "*" not in granted and permission not in granted:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "permission_denied",
                "message": f"Permission required: {permission}",
            },
        )


async def require_owned_session(
    container: ApiContainer, identity: Identity, session_id: str
) -> Session:
    session = await container.sessions.get(identity.tenant_id, session_id)
    if session.user_id != identity.user_id:
        raise NotFoundError(f"session not found: {session_id}")
    return session


async def require_owned_run(container: ApiContainer, identity: Identity, run_id: str) -> Run:
    run = await container.runs.get(identity.tenant_id, run_id)
    await require_owned_session(container, identity, run.session_id)
    return run
