"""Production composition root using PostgreSQL, Redis, MinIO and Claude SDK."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select

from harness.agui.service import AguiRunService
from harness.agui.task_title import ControlPlaneTaskTitleGenerator
from harness.api.dependencies import ApiContainer
from harness.application.agent_assets import (
    resolve_published_agent_versions,
    stage_published_agent_assets,
)
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
from harness.auth.api_access import ApiAccessService
from harness.auth.audit import AuditService
from harness.auth.repositories import PostgresAuditRepository, PostgresAuthRepository
from harness.auth.service import AuthService, OAuthProviderConfig
from harness.config import Settings
from harness.context.checkpoint import ContextCheckpointService
from harness.context.service import ContextService
from harness.core.manifest import AgentManifest, AgentManifestSnapshot
from harness.core.models import RunStatus, Session
from harness.core.ports import ArtifactStore, TaskQueue
from harness.deployments.controller import DeploymentController
from harness.deployments.queue import DeploymentTaskQueue
from harness.deployments.service import DeploymentService
from harness.evals.controller import EvalController
from harness.evals.queue import EvalTaskQueue
from harness.evals.service import EvalControlPlaneService
from harness.execution.credentials import (
    BrokerMcpCredentialProvider,
    CredentialResourceKind,
    CredentialSourceKey,
    InMemoryCredentialBroker,
)
from harness.governance.service import GovernanceService
from harness.inputs.processors import DefaultInputProcessor
from harness.knowledge.service import KnowledgeService
from harness.knowledge.workload import (
    KnowledgeWorkloadTokenService,
    RemoteKnowledgeMcpProvider,
    build_knowledge_mcp_app,
)
from harness.lifecycle.adapters import EmptyLifecycleAdapter, LifecycleAdapter
from harness.lifecycle.controller import DataLifecycleController
from harness.lifecycle.models import LifecycleScope, LifecycleScopeKind
from harness.lifecycle.service import DataLifecycleService
from harness.memory_bank.service import MemoryBankService
from harness.memory_bank.workload import (
    MemoryWorkloadTokenService,
    RemoteMemoryMcpProvider,
    build_memory_mcp_app,
)
from harness.observability.provider import build_observability
from harness.platform_mcp.workload import (
    PlatformMcpTokenService,
    build_platform_mcp_app,
)
from harness.policy.profiles import default_policy_profiles
from harness.policy.runtime import ResolvedPolicy
from harness.quality.controller import QualitySyncController
from harness.quality.langfuse import DisabledQualityExporter, LangfuseQualityExporter
from harness.quality.queue import QualityTaskQueue
from harness.quality.service import QualityService
from harness.quota.models import QuotaResource
from harness.quota.service import QuotaService
from harness.reliability.adapters import ObservedEventRepository
from harness.reliability.controller import MaintenanceReaper, ReliabilityController
from harness.reliability.metrics import ReliabilityMetrics
from harness.reliability.probes import CapacityProbe, QueueStats
from harness.reliability.service import ReliabilityService
from harness.runtime.codex_tool_gate import CodexToolGate
from harness.runtime.default_tools import (
    TAVILY_REFERENCE,
    default_tool_resolver,
    server_secret_credential_provider,
)
from harness.runtime.fake import FakeRuntime
from harness.runtime.installed import INSTALLED_AGENT_RUNTIMES
from harness.runtime.mcp_credentials import DynamicMcpCredentialProvider
from harness.runtime.registry_codex_runtime import RegistryCodexRuntime, RegistryRuntimeRouter
from harness.runtime.registry_runtime import RegistryClaudeRuntime
from harness.runtime.sdk_tool_gate import SdkToolGate
from harness.runtime.session_store import PostgresSessionStore
from harness.sandbox.base import SandboxProvider
from harness.sandbox.daytona import DaytonaSandboxProvider, SdkDaytonaClient
from harness.sandbox.deferred import DeferredToolSandboxProvider
from harness.sandbox.e2b import E2BSandboxProvider, SdkE2BClient
from harness.sandbox.kubernetes import (
    KubectlKubernetesClient,
    KubernetesSandboxProvider,
)
from harness.sandbox.local import LocalSandboxProvider
from harness.sharing.service import TeamSpaceService
from harness.sharing.workspace_repositories import AgentIdentityService
from harness.storage.api_access_repository import PostgresApiAccessKeyRepository
from harness.storage.catalog_repository import PostgresCapabilityCatalogRepository
from harness.storage.context_repository import PostgresContextRepository
from harness.storage.database import create_database
from harness.storage.deployment_repository import (
    PostgresDeploymentRepository,
    PostgresEnvironmentRepository,
)
from harness.storage.eval_repository import (
    PostgresEvalDatasetRepository,
    PostgresEvalRunRepository,
)
from harness.storage.governance_repository import PostgresGovernanceRepository
from harness.storage.knowledge_repository import PostgresKnowledgeRepository
from harness.storage.lifecycle_adapters import (
    LangfuseLifecycleAdapter,
    MemoryLifecycleAdapter,
    ObjectStoreLifecycleAdapter,
    PostgresLifecycleAdapter,
    SdkSessionLifecycleAdapter,
)
from harness.storage.lifecycle_repository import PostgresDataLifecycleRepository
from harness.storage.mcp_credential_repository import PostgresMcpCredentialRepository
from harness.storage.memory_bank_repository import PostgresMemoryBankRepository
from harness.storage.minio import MinioArtifactStore
from harness.storage.models import UsageLedgerRow
from harness.storage.platform_repositories import (
    PostgresAgentRegistry,
    PostgresAguiThreadBindingRepository,
    PostgresApprovalRepository,
    PostgresArtifactRepository,
    PostgresInputArtifactRepository,
    PostgresSessionRepository,
    PostgresThreadFileRepository,
    PostgresUserMemoryRepository,
    PostgresWorkspaceSnapshotRepository,
)
from harness.storage.preview_repository import PostgresPreviewRepository
from harness.storage.quality_repository import PostgresQualityRepository
from harness.storage.quota_repository import PostgresQuotaRepository
from harness.storage.redis import (
    AsyncRedisClient,
    RedisCancellationWakeup,
    RedisEventBus,
    RedisTaskQueue,
)
from harness.storage.reliability_repository import PostgresReliabilityRepository
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository
from harness.storage.sharing_repository import PostgresTeamSpaceRepository
from harness.storage.studio_repository import PostgresAgentDraftRepository
from harness.storage.transcript_checkpoint import PostgresTranscriptCheckpointProvider
from harness.storage.trigger_repository import PostgresAgentTriggerRepository
from harness.storage.workspace_repository import PostgresWorkspaceAgentRepository
from harness.studio.catalog import default_capability_catalog
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.mcp_credential_store import (
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
    ControlPlaneModelPreflightProbe,
    FakeMcpPreflightProbe,
    FakeModelPreflightProbe,
    StreamableHttpMcpProbe,
)
from harness.studio.preview_controller import PreviewController
from harness.studio.preview_queue import PreviewTaskQueue
from harness.studio.preview_service import PreviewService
from harness.studio.service import AgentStudioService
from harness.studio.skill_builder import ControlPlaneSkillConversationService
from harness.triggers.service import AgentTriggerService
from harness.worker.orchestrator import RunOrchestrator, SandboxResolver


def _sandbox(settings: Settings) -> SandboxProvider:
    if settings.sandbox_provider == "local":
        if not settings.allow_unsafe_local_sandbox:
            raise ValueError(
                "production local sandbox requires "
                "HARNESS_ALLOW_UNSAFE_LOCAL_SANDBOX=true; use Daytona for untrusted Agents"
            )
        return LocalSandboxProvider()
    if settings.sandbox_provider == "kubernetes":
        if not settings.kubernetes_image:
            raise ValueError("HARNESS_KUBERNETES_IMAGE is required for Kubernetes")
        try:
            selector_raw = json.loads(settings.kubernetes_egress_gateway_selector_json)
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
        if not settings.kubernetes_egress_proxy_url:
            raise ValueError("HARNESS_KUBERNETES_EGRESS_PROXY_URL is required for Kubernetes")
        client = KubectlKubernetesClient(
            namespace=settings.kubernetes_namespace,
            kubectl_path=settings.kubernetes_kubectl_path,
            kubeconfig=settings.kubernetes_kubeconfig or None,
            context=settings.kubernetes_context or None,
        )
        return KubernetesSandboxProvider(
            client=client,
            namespace=settings.kubernetes_namespace,
            image=settings.kubernetes_image,
            runtime_class_name=settings.kubernetes_runtime_class_name,
            service_account_name=settings.kubernetes_service_account_name,
            remote_workspace=settings.kubernetes_remote_workspace,
            cli_version=settings.kubernetes_claude_cli_version,
            cli_path=settings.kubernetes_claude_cli_path,
            ttl_seconds=settings.kubernetes_pod_ttl_seconds,
            ready_timeout_seconds=settings.kubernetes_ready_timeout_seconds,
            cpu_millis=settings.kubernetes_cpu_millis,
            memory_mib=settings.kubernetes_memory_mib,
            disk_mib=settings.kubernetes_disk_mib,
            egress_gateway_namespace=settings.kubernetes_egress_gateway_namespace,
            egress_gateway_selector=cast(dict[str, str], selector_items),
            egress_gateway_port=settings.kubernetes_egress_gateway_port,
            egress_proxy_url=settings.kubernetes_egress_proxy_url,
            dns_namespace=settings.kubernetes_dns_namespace,
            max_collect_bytes=settings.workspace_archive_max_bytes,
            max_collect_members=settings.workspace_archive_max_members,
        )
    if settings.sandbox_provider == "e2b":
        api_key = settings.e2b_api_key.get_secret_value()
        if not api_key:
            raise ValueError("HARNESS_E2B_API_KEY is required for E2B")
        return E2BSandboxProvider(
            client=SdkE2BClient(api_key=api_key),
            template=settings.e2b_template,
            timeout_seconds=settings.e2b_timeout_seconds,
            allow_internet_access=settings.e2b_allow_internet_access,
            remote_workspace_root=settings.e2b_remote_workspace_root,
            cli_version=settings.e2b_claude_cli_version,
            cli_path=settings.e2b_claude_cli_path,
            max_collect_bytes=settings.workspace_archive_max_bytes,
            max_collect_members=settings.workspace_archive_max_members,
        )
    api_key = settings.daytona_api_key.get_secret_value()
    if not api_key:
        raise ValueError("HARNESS_DAYTONA_API_KEY is required for Daytona")
    return DaytonaSandboxProvider(
        client=SdkDaytonaClient.from_config(
            api_key=api_key,
            api_url=settings.daytona_api_url or None,
            target=settings.daytona_target or None,
        ),
        snapshot=settings.daytona_snapshot or None,
        remote_workspace_root=settings.daytona_remote_workspace_root,
        cli_version=settings.daytona_claude_cli_version,
        cli_path=settings.daytona_claude_cli_path,
        codex_cli_version=settings.daytona_codex_cli_version,
        codex_cli_path=settings.daytona_codex_cli_path,
        codex_cli_sha256=settings.daytona_codex_cli_sha256,
        delete_on_destroy=settings.daytona_delete_on_destroy,
        auto_stop_interval_minutes=settings.daytona_auto_stop_interval_minutes,
        auto_delete_interval_minutes=settings.daytona_auto_delete_interval_minutes,
        session_reuse_enabled=settings.daytona_session_reuse_enabled,
        session_idle_timeout_seconds=settings.daytona_session_idle_timeout_seconds,
        warm_pool_max_sessions=settings.daytona_warm_pool_max_sessions,
        recovery_retention_seconds=settings.daytona_recovery_retention_seconds,
        max_collect_bytes=settings.workspace_archive_max_bytes,
        max_collect_members=settings.workspace_archive_max_members,
    )


def _runtime_sandbox(
    settings: Settings,
    backend: SandboxProvider,
) -> SandboxProvider:
    if settings.sandbox_execution_mode == "remote_cli":
        return backend
    if settings.sandbox_provider == "local":
        raise ValueError(
            "HARNESS_SANDBOX_EXECUTION_MODE=worker_cli_deferred requires "
            "Daytona, E2B, or Kubernetes"
        )
    return DeferredToolSandboxProvider(
        backend,
        provider_name=settings.sandbox_provider,
        max_active_runs=settings.worker_deferred_max_active_runs,
    )


def _manifests_require_remote_cli(
    manifests: tuple[AgentManifest, ...],
    *,
    read_only_mcp_references: frozenset[str],
) -> bool:
    """Keep executable Python and non-read-only external MCP away from the Worker."""

    for manifest in manifests:
        for tool in manifest.spec.tools:
            if tool.python_entry is not None:
                return True
            if tool.mcp is not None and tool.mcp not in read_only_mcp_references:
                return True
    return False


def build_production_container(
    settings: Settings, *, execution_enabled: bool = True
) -> ApiContainer:
    if settings.environment != "production":
        raise ValueError("production composition requires HARNESS_ENVIRONMENT=production")
    if settings.runtime not in {"claude-sdk", "multi"}:
        raise ValueError("production composition requires HARNESS_RUNTIME=claude-sdk or multi")
    access_key = settings.minio_access_key.get_secret_value()
    secret_key = settings.minio_secret_key.get_secret_value()
    if not access_key or not secret_key:
        raise ValueError("production requires HARNESS_MINIO_ACCESS_KEY and SECRET_KEY")
    execution_config: (
        tuple[
            SandboxProvider,
            SandboxProvider,
            DynamicMcpCredentialProvider,
            InMemoryCredentialBroker,
        ]
        | None
    ) = None
    preflight_sandbox: SandboxProvider | None = None
    sandbox_maintenance: Callable[[], Awaitable[object]] | None = None
    credential_broker: InMemoryCredentialBroker | None = None
    # Production model routes and credentials are tenant control-plane data.
    # Environment-backed gateways remain supported by the local/dev composer,
    # but are deliberately ignored by the production container.
    if execution_enabled:
        sandbox_backend = _sandbox(settings)
        preflight_sandbox = sandbox_backend
        if isinstance(sandbox_backend, KubernetesSandboxProvider):
            sandbox_maintenance = sandbox_backend.reap_expired
        elif isinstance(sandbox_backend, DaytonaSandboxProvider):
            sandbox_maintenance = sandbox_backend.reap_expired
        sandbox = _runtime_sandbox(settings, sandbox_backend)
        references_raw = json.loads(settings.mcp_secret_references_json)
        secrets_raw = json.loads(settings.mcp_server_secrets_json.get_secret_value())
        if not isinstance(references_raw, dict) or not isinstance(secrets_raw, dict):
            raise ValueError("MCP credential settings must be JSON objects")
        typed_secrets = cast(dict[object, object], secrets_raw)
        sources: dict[CredentialSourceKey, tuple[str, dict[str, SecretStr]]] = {}
        for server, raw_references in cast(dict[object, object], references_raw).items():
            if not isinstance(raw_references, dict):
                continue
            values = {
                str(key): SecretStr(str(typed_secrets[secret_reference]))
                for key, secret_reference in cast(dict[object, object], raw_references).items()
                if secret_reference in typed_secrets
            }
            sources[("*", CredentialResourceKind.MCP, str(server))] = (
                f"settings://mcp/{server}",
                values,
            )
        credential_broker = InMemoryCredentialBroker(
            sources,
            clock=lambda: datetime.now(UTC),
        )
        credential_provider = BrokerMcpCredentialProvider(credential_broker)
        execution_config = (
            sandbox,
            sandbox_backend,
            credential_provider,
            credential_broker,
        )

    engine, sessions = create_database(settings.database_url)
    redis = Redis.from_url(settings.redis_url)  # pyright: ignore[reportUnknownMemberType]
    redis_client = cast(AsyncRedisClient, redis)
    registry = PostgresAgentRegistry(sessions)
    team_space_repository = PostgresTeamSpaceRepository(sessions)
    session_repository = PostgresSessionRepository(sessions)
    runs = PostgresRunRepository(sessions)
    approvals = PostgresApprovalRepository(sessions)
    artifact_repository = PostgresArtifactRepository(sessions)
    input_repository = PostgresInputArtifactRepository(sessions)
    memory_repository = PostgresUserMemoryRepository(sessions)
    memory_bank_repository = PostgresMemoryBankRepository(sessions)
    knowledge_repository = PostgresKnowledgeRepository(sessions)
    governance_repository = PostgresGovernanceRepository(sessions)
    file_repository = PostgresThreadFileRepository(sessions)
    snapshot_repository = PostgresWorkspaceSnapshotRepository(sessions)
    binding_repository = PostgresAguiThreadBindingRepository(sessions)
    raw_event_repository = PostgresEventRepository(sessions)
    agent_drafts = PostgresAgentDraftRepository(sessions)
    preview_repository = PostgresPreviewRepository(sessions)
    eval_dataset_repository = PostgresEvalDatasetRepository(sessions)
    eval_run_repository = PostgresEvalRunRepository(sessions)
    environment_repository = PostgresEnvironmentRepository(sessions)
    deployment_repository = PostgresDeploymentRepository(sessions)
    trigger_repository = PostgresAgentTriggerRepository(sessions)
    quality_repository = PostgresQualityRepository(sessions)
    capability_catalog_repository = PostgresCapabilityCatalogRepository(sessions)
    mcp_credential_repository = PostgresMcpCredentialRepository(sessions)
    auth = AuthService(
        PostgresAuthRepository(sessions),
        jwt_secret=settings.auth_jwt_secret,
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        access_token_minutes=settings.auth_access_token_minutes,
        refresh_token_days=settings.auth_refresh_token_days,
        allow_registration=settings.auth_allow_registration,
        default_tenant_id=settings.auth_default_tenant_id,
        google=OAuthProviderConfig(
            settings.auth_google_client_id, settings.auth_google_client_secret
        ),
        github=OAuthProviderConfig(
            settings.auth_github_client_id, settings.auth_github_client_secret
        ),
    )
    audit = AuditService(PostgresAuditRepository(sessions))
    api_access = ApiAccessService(PostgresApiAccessKeyRepository(sessions), audit=audit)
    policy_profiles = default_policy_profiles()
    governance = GovernanceService(
        governance_repository,
        static_profiles=policy_profiles,
        audit=audit,
    )
    if credential_broker is not None:
        credential_broker.set_connection_authorizer(governance)
    if settings.worker_task_heartbeat_seconds >= settings.worker_task_visibility_timeout_seconds:
        raise ValueError("worker task heartbeat must be shorter than visibility timeout")
    queue: TaskQueue = RedisTaskQueue(
        redis_client,
        visibility_timeout_seconds=settings.worker_task_visibility_timeout_seconds,
        retry_delay_seconds=settings.worker_task_retry_delay_seconds,
    )
    bus = RedisEventBus(redis_client)
    cancellation_wakeup = RedisCancellationWakeup(redis_client)
    store: ArtifactStore = MinioArtifactStore(
        endpoint=settings.minio_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=settings.minio_bucket,
        secure=settings.minio_secure,
    )
    observability = build_observability(settings)
    reliability_metrics = ReliabilityMetrics()
    observed_event_repository = ObservedEventRepository(raw_event_repository, reliability_metrics)

    def clock() -> datetime:
        return datetime.now(UTC)

    def ids(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    context_service = ContextService(
        PostgresContextRepository(sessions),
        clock=clock,
        id_generator=ids,
    )
    context_checkpoints = ContextCheckpointService(
        context_service,
        PostgresTranscriptCheckpointProvider(sessions),
    )
    knowledge = KnowledgeService(
        knowledge_repository,
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    quotas = QuotaService(
        PostgresQuotaRepository(sessions),
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    enforced_quotas = quotas if settings.quota_enforcement_enabled else None
    lifecycle_repository = PostgresDataLifecycleRepository(sessions)
    lifecycle_adapters: tuple[LifecycleAdapter, ...] = (
        ObjectStoreLifecycleAdapter(sessions, store),
        SdkSessionLifecycleAdapter(sessions),
        MemoryLifecycleAdapter(sessions),
        (
            LangfuseLifecycleAdapter(
                sessions,
                base_url=settings.langfuse_base_url,
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
            )
            if settings.langfuse_base_url
            and settings.langfuse_public_key
            and settings.langfuse_secret_key.get_secret_value()
            else EmptyLifecycleAdapter("langfuse")
        ),
        # PostgreSQL must remain last: it contains the indexes required to
        # identify external data during an eventually-consistent retry.
        PostgresLifecycleAdapter(sessions),
    )

    async def lifecycle_scopes(tenant_id: str, scope: LifecycleScope) -> tuple[LifecycleScope, ...]:
        if scope.kind is not LifecycleScopeKind.SESSION:
            return (scope,)
        session = await session_repository.get(tenant_id, scope.subject_id)
        return (
            scope,
            LifecycleScope(kind=LifecycleScopeKind.USER, subjectId=session.user_id),
            LifecycleScope(kind=LifecycleScopeKind.AGENT, subjectId=session.agent_name),
        )

    lifecycle = DataLifecycleService(
        lifecycle_repository,
        lifecycle_adapters,
        export_store=store,
        scope_resolver=lifecycle_scopes,
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    lifecycle_controller = DataLifecycleController(
        lifecycle_repository,
        lifecycle_adapters,
        store,
        scope_resolver=lifecycle_scopes,
        clock=clock,
    )

    default_agent_manifest = Path("/app/agents/lead-agent/agent.yaml")
    if not default_agent_manifest.exists():
        default_agent_manifest = Path("agents/lead-agent/agent.yaml")
    workspace_agent_repository = PostgresWorkspaceAgentRepository(sessions)
    agent_ids = AgentIdentityService(
        workspace_agent_repository,
        clock=clock,
        id_generator=ids,
    )
    agent_service = AgentService(
        registry,
        clock=clock,
        environment="production",
        default_manifest_path=default_agent_manifest,
        agent_ids=agent_ids,
    )
    team_spaces = TeamSpaceService(
        team_space_repository,
        workspace_agent_repository,
        registry,
        drafts=agent_drafts,
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    knowledge.configure_team_grant_checker(team_spaces.has_knowledge_access)
    capability_catalogs = CapabilityCatalogService(
        capability_catalog_repository,
        agent_drafts,
        clock=clock,
    )
    environment_mcp_credentials = server_secret_credential_provider(
        references_json=settings.mcp_secret_references_json,
        secrets_json=settings.mcp_server_secrets_json.get_secret_value(),
    )
    mcp_credential_service = McpCredentialService(
        mcp_credential_repository,
        McpCredentialCipher(settings.auth_jwt_secret),
        audit=audit,
    )
    model_configurations = ModelConfigurationService(
        capability_catalogs,
        mcp_credential_service,
        environment="production",
    )
    discovery_credentials = StoredMcpCredentialProvider(
        mcp_credential_service,
        environment_mcp_credentials,
    )
    mcp_discovery = McpDiscoveryService(
        credentials=discovery_credentials,
        connector=AutoDetectMcpConnector(
            proxy_url=settings.mcp_discovery_proxy_url.get_secret_value()
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
        id_generator=lambda: ids("draft"),
    )
    preview_queue = PreviewTaskQueue.redis(
        redis_client,
        visibility_timeout_seconds=settings.worker_task_visibility_timeout_seconds,
        retry_delay_seconds=settings.worker_task_retry_delay_seconds,
    )
    eval_queue = EvalTaskQueue.redis(
        redis_client,
        visibility_timeout_seconds=settings.worker_task_visibility_timeout_seconds,
        retry_delay_seconds=settings.worker_task_retry_delay_seconds,
    )
    deployment_queue = DeploymentTaskQueue.redis(
        redis_client,
        visibility_timeout_seconds=settings.worker_task_visibility_timeout_seconds,
        retry_delay_seconds=settings.worker_task_retry_delay_seconds,
    )
    quality_queue = QualityTaskQueue.redis(
        redis_client,
        visibility_timeout_seconds=settings.worker_task_visibility_timeout_seconds,
        retry_delay_seconds=settings.worker_task_retry_delay_seconds,
    )
    preview_service = PreviewService(
        repository=preview_repository,
        queue=preview_queue,
        studio=studio_service,
        audit=audit,
        clock=clock,
        id_generator=lambda: ids("preview"),
        quotas=enforced_quotas,
    )
    events = EventService(
        raw_event_repository,
        bus,
        clock=clock,
        id_generator=ids,
        trace_context=observability,
    )
    session_service = SessionService(
        registry,
        session_repository,
        clock=clock,
        id_generator=ids,
        require_published_dependencies=True,
        knowledge_binding_resolver=knowledge.resolve_bindings,
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
                else settings.run_reservation_ttl_seconds
            ),
        )

    run_service = RunService(
        session_repository,
        runs,
        queue,
        events,
        clock=clock,
        id_generator=ids,
        observability=observability,
        metrics=reliability_metrics,
        admission=enforced_quotas,
        quota_plan_resolver=run_quota_plan,
        cancellation_wakeup=cancellation_wakeup,
    )
    trigger_service = AgentTriggerService(
        trigger_repository,
        sessions=session_service,
        runs=run_service,
        registry=registry,
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    approval_service = ApprovalService(
        runs=runs,
        approvals=approvals,
        events=events,
        clock=clock,
        id_generator=ids,
        queue=queue,
        observability=observability,
        metrics=reliability_metrics,
    )
    artifact_service = ArtifactService(
        runs=runs,
        repository=artifact_repository,
        store=store,
        id_generator=ids,
        max_file_bytes=settings.output_artifact_max_bytes,
        sessions=session_repository,
        quotas=enforced_quotas,
    )
    file_service = FileCatalogService(file_repository, clock=clock, id_generator=ids)
    input_service = InputArtifactService(
        repository=input_repository,
        store=store,
        id_generator=ids,
        clock=clock,
        processor=DefaultInputProcessor(),
        file_catalog=file_service,
    )
    eval_service = EvalControlPlaneService(
        datasets=eval_dataset_repository,
        runs=eval_run_repository,
        queue=eval_queue,
        studio=studio_service,
        registry=registry,
        object_store=store,
        previews=preview_service,
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    eval_controller = EvalController(
        datasets=eval_dataset_repository,
        repository=eval_run_repository,
        queue=eval_queue,
        sessions=session_service,
        runs=run_service,
        events=events,
        inputs=input_service,
        object_store=store,
        clock=clock,
    )
    quality_service = QualityService(
        repository=quality_repository,
        queue=quality_queue,
        runs=runs,
        sessions=session_repository,
        events=raw_event_repository,
        artifacts=artifact_repository,
        metrics=reliability_metrics,
        clock=clock,
    )
    quality_exporter = (
        LangfuseQualityExporter(
            base_url=settings.langfuse_base_url,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
        )
        if settings.langfuse_base_url
        and settings.langfuse_public_key
        and settings.langfuse_secret_key.get_secret_value()
        else DisabledQualityExporter()
    )
    quality_controller = QualitySyncController(
        repository=quality_repository,
        queue=quality_queue,
        exporter=quality_exporter,
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
        id_generator=ids,
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
    memory_bank = MemoryBankService(
        memory_bank_repository,
        audit=audit,
        clock=clock,
        id_generator=ids,
    )
    memory_tokens = MemoryWorkloadTokenService(settings.memory_workload_token_secret)
    memory_mcp_app = build_memory_mcp_app(memory_bank, memory_tokens)
    remote_memory_mcp = RemoteMemoryMcpProvider(settings.memory_mcp_public_url, memory_tokens)
    knowledge_tokens = KnowledgeWorkloadTokenService(settings.knowledge_workload_token_secret)
    knowledge_mcp_app = build_knowledge_mcp_app(knowledge, knowledge_tokens)
    remote_knowledge_mcp = RemoteKnowledgeMcpProvider(
        settings.knowledge_mcp_public_url,
        knowledge_tokens,
    )
    platform_mcp_tokens = PlatformMcpTokenService(settings.auth_jwt_secret)
    platform_mcp_app = build_platform_mcp_app(
        agents=agent_service,
        deployments=deployment_service,
        quotas=quotas,
        governance=governance,
        tokens=platform_mcp_tokens,
    )
    memory_service = UserMemoryService(memory_repository, clock=clock, memory_bank=memory_bank)
    workspace_service = WorkspaceService(
        store,
        snapshots=snapshot_repository,
        max_archive_bytes=settings.workspace_archive_max_bytes,
        max_archive_members=settings.workspace_archive_max_members,
        sessions=session_repository,
        quotas=enforced_quotas,
    )

    async def workspace_policy(
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

    policy = policy_profiles.resolve("local-standard")
    sandbox_resolver: SandboxResolver | None = None
    if execution_enabled:
        assert execution_config is not None
        (
            runtime_sandbox,
            runtime_sandbox_backend,
            credential_provider,
            credential_broker,
        ) = execution_config
        credential_provider = StoredMcpCredentialProvider(
            mcp_credential_service,
            credential_provider,
        )
        tool_resolver = default_tool_resolver(
            credential_provider,
            catalogs=capability_catalogs,
        )
        claude_runtime = RegistryClaudeRuntime(
            registry=registry,
            model_configurations=model_configurations,
            tool_resolver=tool_resolver,
            tool_gate=SdkToolGate(
                profiles=policy_profiles,
                approvals=approval_service,
                events=events,
                context_service=context_service,
                quotas=enforced_quotas,
                observability=observability,
            ),
            memory_service=memory_service,
            memory_bank=memory_bank,
            remote_memory_mcp=remote_memory_mcp,
            knowledge=knowledge,
            remote_knowledge_mcp=remote_knowledge_mcp,
            session_store_factory=lambda session: PostgresSessionStore(
                sessions,
                tenant_id=session.tenant_id,
                project_id=session.session_id,
            ),
            observability=observability,
            credential_broker=credential_broker,
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
                                codex_path=Path(settings.codex_cli_path),
                                model_configurations=model_configurations,
                                tool_resolver=tool_resolver,
                                model_by_route=settings.codex_model_by_route,
                                provider_by_route=settings.codex_provider_by_route,
                                approval_policy=settings.codex_approval_policy,
                                network_access=settings.codex_network_access,
                                tool_output_token_limit=settings.codex_tool_output_token_limit,
                                server_request_handler=CodexToolGate(
                                    approvals=approval_service,
                                    events=events,
                                ).authorize,
                            ),
                        ),
                    )
                ),
            )
            if settings.runtime == "multi"
            else claude_runtime
        )
        model_probe = ControlPlaneModelPreflightProbe(model_configurations)
        mcp_probe = StreamableHttpMcpProbe(tool_resolver)

        async def resolve_runtime_sandbox(tenant_id: str, session: Session) -> SandboxProvider:
            if session.deployment_snapshot_id is not None:
                snapshot = await deployment_repository.get_snapshot(
                    tenant_id, session.deployment_snapshot_id
                )
                profile = next(
                    (
                        item
                        for item in default_capability_catalog().execution_profiles
                        if item.profile_id == snapshot.execution_profile
                        and item.version == snapshot.execution_profile_version
                    ),
                    None,
                )
                if profile is None:
                    raise RuntimeError("deployment_execution_profile_unavailable")
                actual = (
                    "gvisor"
                    if isinstance(runtime_sandbox_backend, KubernetesSandboxProvider)
                    else "e2b"
                    if isinstance(runtime_sandbox_backend, E2BSandboxProvider)
                    else "daytona"
                    if isinstance(runtime_sandbox_backend, DaytonaSandboxProvider)
                    else "local"
                )
                if profile.sandbox_provider != actual:
                    raise RuntimeError("execution_profile_sandbox_provider_mismatch")

            if settings.sandbox_execution_mode != "worker_cli_deferred":
                return runtime_sandbox_backend

            root, children = await resolve_published_agent_versions(
                registry,
                tenant_id=tenant_id,
                owner_user_id=session.resolved_agent_owner_user_id,
                agent_name=session.agent_name,
                agent_version=session.agent_version,
                allow_validated_graph=session.environment == "preview",
            )
            manifests = tuple(
                AgentManifestSnapshot.model_validate(version.snapshot).manifest
                for version in (root, *children.values())
            )
            catalog = (await capability_catalogs.get(tenant_id)).catalog
            read_only_mcp_references = frozenset(
                {
                    TAVILY_REFERENCE,
                    *(
                        capability.reference
                        for capability in catalog.mcp_servers
                        if capability.enabled and capability.read_only
                    ),
                }
            )
            if _manifests_require_remote_cli(
                manifests,
                read_only_mcp_references=read_only_mcp_references,
            ):
                return runtime_sandbox_backend
            return runtime_sandbox

        sandbox_resolver = resolve_runtime_sandbox
    else:
        runtime = FakeRuntime()
        runtime_sandbox = LocalSandboxProvider()
        runtime_sandbox_backend = runtime_sandbox
        preflight_sandbox = runtime_sandbox
        model_probe = FakeModelPreflightProbe()
        mcp_probe = FakeMcpPreflightProbe()
    assert preflight_sandbox is not None
    preflight_runner = LivePreflightRunner(
        studio=studio_service,
        sandbox=preflight_sandbox,
        model_probe=model_probe,
        mcp_probe=mcp_probe,
        policies=policy_profiles,
        policy_resolver=governance.resolve_runtime,
        observability=observability,
        timeout_seconds=settings.preflight_timeout_seconds,
        clock=clock,
        enforce_execution_profile_provider=True,
    )
    preview_controller = PreviewController(
        repository=preview_repository,
        queue=preview_queue,
        provisioner=LivePreflightProvisioner(
            runner=preflight_runner,
            repository=preview_repository,
            clock=clock,
        ),
        heartbeat_seconds=settings.worker_task_heartbeat_seconds,
        clock=clock,
        quotas=enforced_quotas,
    )
    worker = RunOrchestrator(
        sessions=session_repository,
        runs=runs,
        events=events,
        runtime=runtime,
        sandbox=runtime_sandbox,
        clock=clock,
        policy=policy,
        approvals=approval_service,
        workspaces=workspace_service,
        observability=observability,
        artifacts=artifact_service,
        input_artifacts=input_service,
        memory=memory_service,
        workspace_policy_resolver=workspace_policy,
        runtime_asset_stager=stage_runtime_assets if execution_enabled else None,
        policy_resolver=resolve_policy,
        output_artifact_max_bytes=settings.output_artifact_max_bytes,
        quality_hook=quality_service.record_terminal_run,
        credential_revoker=(
            credential_broker.revoke_run if credential_broker is not None else None
        ),
        sandbox_resolver=sandbox_resolver,
        quotas=enforced_quotas,
        quota_plan_resolver=run_quota_plan,
        metrics=reliability_metrics,
        cancellation_wakeup=cancellation_wakeup,
        context_checkpoints=context_checkpoints,
        context_service=context_service,
    )
    agui = AguiRunService(
        sessions=session_service,
        runs=run_service,
        input_artifacts=input_service,
        bindings=binding_repository,
        contexts=context_service,
        title_generator=ControlPlaneTaskTitleGenerator(model_configurations),
    )

    async def infrastructure_facts(tenant_id: str) -> dict[str, int | None]:
        async with sessions() as db:
            rows = (
                await db.execute(
                    select(
                        UsageLedgerRow.resource,
                        func.coalesce(func.sum(UsageLedgerRow.amount), 0),
                    )
                    .where(
                        UsageLedgerRow.tenant_id == tenant_id,
                        UsageLedgerRow.resource.in_(
                            (
                                QuotaResource.ARTIFACT_BYTES.value,
                                QuotaResource.SNAPSHOT_BYTES.value,
                            )
                        ),
                    )
                    .group_by(UsageLedgerRow.resource)
                )
            ).all()
        totals = {str(resource): int(total) for resource, total in rows}
        checked_out = getattr(engine.pool, "checkedout", None)
        active_sandboxes = (
            await runtime_sandbox_backend.active_count()
            if isinstance(runtime_sandbox_backend, KubernetesSandboxProvider)
            else None
        )
        return {
            "active_sandboxes": active_sandboxes,
            "database_pool_checked_out": (
                int(cast(Callable[[], Any], checked_out)()) if callable(checked_out) else None
            ),
            "artifact_bytes": totals.get(QuotaResource.ARTIFACT_BYTES.value, 0),
            "snapshot_bytes": totals.get(QuotaResource.SNAPSHOT_BYTES.value, 0),
        }

    async def lifecycle_reap() -> int:
        await lifecycle.enqueue_due_retention_jobs()
        return int(await lifecycle_controller.process_once() is not None)

    reliability_repository = PostgresReliabilityRepository(sessions)
    capacity_probe = CapacityProbe(
        runs=runs,
        approvals=approvals,
        previews=preview_repository,
        queue=cast(QueueStats, queue),
        lifecycle=lifecycle_repository,
        credentials=credential_broker,
        infrastructure_facts=infrastructure_facts,
    )
    reliability = ReliabilityService(
        reliability_repository,
        reliability_metrics,
        capacity_probe,
        clock=clock,
        id_generator=ids,
    )
    maintenance = [
        MaintenanceReaper("approval-expiry", "approval", approval_service.reap_expired),
        MaintenanceReaper("preview-expiry", "preview", preview_controller.reap_expired),
        MaintenanceReaper("quota-reservation", "quota", quotas.reap_expired_all),
        MaintenanceReaper("workspace-retention", "workspace", lifecycle_reap),
        MaintenanceReaper("memory-expiry", "memory", memory_bank.reap_expired),
    ]
    if credential_broker is not None:
        maintenance.append(
            MaintenanceReaper(
                "credential-lease", "credential_lease", credential_broker.reap_expired
            )
        )
    if sandbox_maintenance is not None:
        maintenance.append(MaintenanceReaper("sandbox-expiry", "sandbox", sandbox_maintenance))
    reliability_controller = ReliabilityController(
        runs=runs,
        events=events,
        repository=reliability_repository,
        metrics=reliability_metrics,
        thresholds={
            RunStatus.QUEUED: settings.stuck_queued_seconds,
            RunStatus.PROVISIONING: settings.stuck_provisioning_seconds,
            RunStatus.RUNNING: settings.stuck_running_seconds,
            RunStatus.WAITING_APPROVAL: settings.stuck_waiting_approval_seconds,
            RunStatus.CANCELLING: settings.stuck_cancelling_seconds,
        },
        maintenance=maintenance,
        quotas=enforced_quotas,
        credentials=credential_broker,
        clock=clock,
        id_generator=ids,
    )

    async def close() -> None:
        await redis.aclose()
        await engine.dispose()

    return ApiContainer(
        environment="production",
        api_bearer_token=settings.api_bearer_token,
        auth=auth,
        api_access=api_access,
        audit=audit,
        agent_drafts=agent_drafts,
        capability_catalogs=capability_catalogs,
        mcp_discovery=mcp_discovery,
        mcp_credentials=mcp_credential_service,
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
        input_artifacts=input_service,
        file_catalog=file_service,
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
        events=raw_event_repository,
        observed_events=observed_event_repository,
        task_queue=queue,
        observability=observability,
        runtime=runtime,
        worker=worker,
        agui=agui,
        auto_execute=False,
        event_wakeup=bus,
        skill_conversation=ControlPlaneSkillConversationService(model_configurations),
        sandbox_maintenance=sandbox_maintenance,
        close=close,
    )
