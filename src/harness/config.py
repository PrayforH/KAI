"""Application configuration."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings shared by the local API and worker processes."""

    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    runtime: Literal["fake", "claude-sdk", "multi"] = "fake"
    sandbox_provider: Literal["local", "daytona", "e2b", "kubernetes"] = "local"
    sandbox_execution_mode: Literal["remote_cli", "worker_cli_deferred"] = "remote_cli"
    allow_unsafe_local_sandbox: bool = False
    cc_switch_settings_path: str = "~/.claude/settings.json"
    codex_cli_path: str = "/usr/local/bin/codex"
    codex_model_by_route: dict[str, str] = Field(default_factory=dict)
    codex_provider_by_route: dict[str, str] = Field(default_factory=dict)
    codex_approval_policy: Literal["untrusted", "on-request", "never"] = "untrusted"
    codex_network_access: bool = False
    otel_enabled: bool = False
    otel_content_capture: Literal["off", "redacted"] = "off"
    otel_content_max_chars: int = Field(default=12_000, ge=256, le=100_000)
    local_auto_execute: bool = False
    api_bearer_token: SecretStr = SecretStr("")

    auth_jwt_secret: SecretStr = SecretStr("local-development-auth-secret-change-before-production")
    auth_issuer: str = "claude-agent-harness"
    auth_audience: str = "claude-agent-harness-api"
    auth_access_token_minutes: int = Field(default=30, ge=5, le=1440)
    auth_refresh_token_days: int = Field(default=30, ge=1, le=365)
    auth_allow_registration: bool = True
    auth_default_tenant_id: str = "local"
    auth_google_client_id: str = ""
    auth_google_client_secret: SecretStr = SecretStr("")
    auth_github_client_id: str = ""
    auth_github_client_secret: SecretStr = SecretStr("")

    memory_workload_token_secret: SecretStr = SecretStr(
        "local-development-memory-workload-secret-change-before-production"
    )
    knowledge_workload_token_secret: SecretStr = SecretStr(
        "local-development-knowledge-workload-secret-change-before-production"
    )
    database_url: str = "postgresql+asyncpg://harness:harness@localhost:5432/harness"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    minio_bucket: str = "harness-artifacts"
    minio_secure: bool = False

    new_api_base_url: str = ""
    new_api_key: SecretStr = SecretStr("")
    new_api_model: str = ""
    new_api_flash_model: str = "deepseek-v4-flash"
    new_api_pro_model: str = "deepseek-v4-pro"
    new_api_auth_scheme: Literal["bearer", "x-api-key"] = "bearer"
    new_api_compatibility: Literal["full", "degraded", "unsupported"] = "full"
    new_api_capabilities: str = "streaming,tool_use"

    minimax_m3_base_url: str = ""
    minimax_m3_api_key: SecretStr = SecretStr("")
    minimax_m3_model: str = "MiniMax-M3"
    minimax_m3_auth_scheme: Literal["bearer", "x-api-key"] = "x-api-key"
    minimax_m3_compatibility: Literal["full", "degraded", "unsupported"] = "full"
    minimax_m3_capabilities: str = "streaming,tool_use,vision"

    glm_5_2_base_url: str = ""
    glm_5_2_api_key: SecretStr = SecretStr("")
    glm_5_2_model: str = "shdata-glm"
    glm_5_2_auth_scheme: Literal["bearer", "x-api-key"] = "bearer"
    glm_5_2_compatibility: Literal["full", "degraded", "unsupported"] = "full"
    glm_5_2_capabilities: str = "streaming,tool_use"

    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = ""
    worker_poll_interval_seconds: float = 0.25
    worker_concurrency: int = Field(default=4, ge=1, le=32)
    worker_deferred_max_active_runs: int = Field(default=2, ge=1, le=32)
    worker_task_visibility_timeout_seconds: float = Field(default=60, gt=0)
    worker_task_retry_delay_seconds: float = Field(default=1, ge=0)
    worker_task_heartbeat_seconds: float = Field(default=20, gt=0)
    run_reservation_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    quota_enforcement_enabled: bool = False
    preflight_timeout_seconds: float = Field(default=180, ge=30, le=900)

    daytona_api_key: SecretStr = SecretStr("")
    daytona_api_url: str = "https://app.daytona.io/api"
    daytona_target: str = ""
    daytona_snapshot: str = ""
    daytona_remote_workspace_root: str = "/home/daytona/harness"
    daytona_claude_cli_version: str = "2.1.206"
    daytona_claude_cli_path: str = "/home/daytona/.local/bin/claude"
    daytona_delete_on_destroy: bool = True
    daytona_auto_stop_interval_minutes: int = Field(default=15, ge=1)
    daytona_auto_delete_interval_minutes: int = Field(default=60, ge=1)
    daytona_session_reuse_enabled: bool = True
    daytona_session_idle_timeout_seconds: int = Field(default=600, ge=30, le=86_400)
    daytona_warm_pool_max_sessions: int = Field(default=3, ge=1, le=1000)
    daytona_recovery_retention_seconds: int = Field(
        default=3600,
        ge=300,
        le=86_400,
    )
    e2b_api_key: SecretStr = SecretStr("")
    e2b_template: str = "base"
    e2b_timeout_seconds: int = Field(default=3600, ge=60, le=86_400)
    e2b_remote_workspace_root: str = "/home/user/harness"
    e2b_claude_cli_version: str = "2.1.206"
    e2b_claude_cli_path: str = "/home/user/.local/bin/claude"
    e2b_allow_internet_access: bool = True
    kubernetes_namespace: str = "harness-sandboxes"
    kubernetes_image: str = ""
    kubernetes_runtime_class_name: str = "gvisor"
    kubernetes_service_account_name: str = "harness-sandbox"
    kubernetes_kubectl_path: str = "kubectl"
    kubernetes_kubeconfig: str = ""
    kubernetes_context: str = ""
    kubernetes_remote_workspace: str = "/workspace"
    kubernetes_claude_cli_version: str = "2.1.206"
    kubernetes_claude_cli_path: str = "/usr/local/bin/claude"
    kubernetes_pod_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    kubernetes_ready_timeout_seconds: float = Field(default=120, ge=10, le=900)
    kubernetes_cpu_millis: int = Field(default=2000, ge=100)
    kubernetes_memory_mib: int = Field(default=4096, ge=256)
    kubernetes_disk_mib: int = Field(default=20_480, ge=256)
    kubernetes_egress_gateway_namespace: str = "harness-system"
    kubernetes_egress_gateway_selector_json: str = (
        '{"app.kubernetes.io/name":"harness-egress-proxy"}'
    )
    kubernetes_egress_proxy_url: str = ""
    kubernetes_egress_gateway_port: int = Field(default=3128, ge=1, le=65_535)
    kubernetes_dns_namespace: str = "kube-system"
    kubernetes_reaper_interval_seconds: float = Field(default=30, ge=5, le=3600)
    reliability_reaper_interval_seconds: float = Field(default=30, ge=5, le=3600)
    worker_metrics_port: int = Field(default=8001, ge=1, le=65_535)
    stuck_queued_seconds: int = Field(default=120, ge=30, le=86_400)
    stuck_provisioning_seconds: int = Field(default=300, ge=30, le=86_400)
    stuck_running_seconds: int = Field(default=3600, ge=60, le=604_800)
    stuck_waiting_approval_seconds: int = Field(default=900, ge=60, le=604_800)
    stuck_cancelling_seconds: int = Field(default=30, ge=10, le=3600)
    output_artifact_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    workspace_archive_max_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    workspace_archive_max_members: int = Field(default=10_000, gt=0)

    mcp_secret_references_json: str = "{}"
    mcp_server_secrets_json: SecretStr = SecretStr("{}")
    mcp_discovery_proxy_url: SecretStr = SecretStr("")

    otlp_endpoint: str = ""
    otlp_headers: SecretStr = SecretStr("")
    otel_service_name: str = "claude-agent-harness"
    otel_environment: str = ""
    langfuse_base_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_dashboard_url: str = ""
    memory_mcp_public_url: str = ""
    knowledge_mcp_public_url: str = ""
