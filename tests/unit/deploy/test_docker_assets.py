from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).parents[3]
COMPOSE_PATH = ROOT / "deploy/docker-compose/compose.yaml"
CODEX_RUNTIME_COMPOSE_PATH = ROOT / "deploy/docker-compose/compose.codex-runtime.yaml"
COLLECTOR_PATH = ROOT / "deploy/otel-collector/collector.yaml"


def compose() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(COMPOSE_PATH.read_text()))


def codex_runtime_compose() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(CODEX_RUNTIME_COMPOSE_PATH.read_text()))


def test_compose_contains_deployable_application_and_infrastructure() -> None:
    services = cast(dict[str, Any], compose()["services"])

    assert {
        "postgres",
        "redis",
        "minio",
        "minio-init",
        "migrate",
        "api",
        "worker",
        "seed",
        "web",
        "otel-collector",
    } <= services.keys()
    assert services["api"]["environment"]["HARNESS_ENVIRONMENT"] == "production"
    assert services["api"]["environment"]["HARNESS_RUNTIME"] == (
        "${HARNESS_RUNTIME:-claude-sdk}"
    )
    assert "build" in services["api"]
    assert services["api"]["image"] == (
        "${HARNESS_API_IMAGE_REPOSITORY:-claude-agent-harness-api}:"
        "${HARNESS_IMAGE_TAG:-local}"
    )
    assert services["web"]["image"] == (
        "${HARNESS_WEB_IMAGE_REPOSITORY:-claude-agent-harness-web}:"
        "${HARNESS_IMAGE_TAG:-local}"
    )
    for name in ("migrate", "worker", "seed"):
        assert "build" not in services[name]
        assert services[name]["image"] == services["api"]["image"]
    assert services["worker"]["environment"]["HARNESS_ENVIRONMENT"] == "production"
    assert "HARNESS_NEW_API_KEY" not in services["worker"]["environment"]
    assert "HARNESS_DAYTONA_API_KEY" in services["worker"]["environment"]
    assert "HARNESS_MCP_SERVER_SECRETS_JSON" in services["worker"]["environment"]
    assert services["worker"]["environment"]["HARNESS_PREFLIGHT_TIMEOUT_SECONDS"] == (
        "${HARNESS_PREFLIGHT_TIMEOUT_SECONDS:-180}"
    )
    for name in ("api", "worker"):
        assert services[name]["environment"]["HARNESS_QUOTA_ENFORCEMENT_ENABLED"] == (
            "${HARNESS_QUOTA_ENFORCEMENT_ENABLED:-false}"
        )
    # Model endpoints and credentials are stored by model management, never injected
    # into API, Worker, or Web container environments.
    assert "HARNESS_NEW_API_KEY" not in services["api"]["environment"]
    assert "HARNESS_NEW_API_KEY" not in services["web"]["environment"]
    assert "HARNESS_DAYTONA_API_KEY" not in services["api"]["environment"]
    assert "HARNESS_MCP_SERVER_SECRETS_JSON" not in services["api"]["environment"]
    assert services["api"]["environment"]["HARNESS_MCP_DISCOVERY_PROXY_URL"] == (
        "${HARNESS_MCP_DISCOVERY_PROXY_URL:-}"
    )
    assert set(services["migrate"]["environment"]) == {"HARNESS_DATABASE_URL"}
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["web"]["depends_on"]["seed"]["condition"] == "service_completed_successfully"
    seed_manifests = services["seed"]["environment"]["HARNESS_SEED_AGENT_MANIFESTS"]
    for manifest in (
        "archive-file-classifier-agent/agent.yaml",
    ):
        assert manifest in seed_manifests
    assert "public-opinion-agent/agent.yaml" not in seed_manifests
    studio_manifests = services["seed"]["environment"]["HARNESS_SEED_STUDIO_MANIFESTS"]
    for manifest in (
        "similar-case-analysis-agent/agent.yaml",
        "govdoc-writer-agent/agent.yaml",
        "archive-assistant-agent/agent.yaml",
    ):
        assert manifest in studio_manifests
    assert "networked-knowledge-research-agent/agent.yaml" not in studio_manifests
    optional_studio_manifests = services["seed"]["environment"][
        "HARNESS_SEED_OPTIONAL_STUDIO_MANIFESTS"
    ]
    assert "public-opinion-agent/agent.yaml" in optional_studio_manifests
    assert "networked-knowledge-research-agent/agent.yaml" in optional_studio_manifests
    assert services["otel-collector"]["profiles"] == ["observability"]
    assert services["postgres"]["image"] == "postgres:18.4-bookworm"
    assert services["postgres"]["volumes"] == [
        "postgres18-cluster-data:/var/lib/postgresql"
    ]
    assert "postgres-data" in compose()["volumes"]
    assert "postgres18-cluster-data" in compose()["volumes"]
    assert "redis-data" in compose()["volumes"]
    assert "minio-data" in compose()["volumes"]


def test_codex_runtime_overlay_is_explicit_and_scoped_to_python_services() -> None:
    overlay = codex_runtime_compose()
    services = cast(dict[str, Any], overlay["services"])

    assert set(services) == {"api", "worker", "quality-sync"}
    for service in services.values():
        environment = cast(dict[str, str], service["environment"])
        assert environment["HARNESS_RUNTIME"] == "multi"
        assert environment["HARNESS_CODEX_CLI_PATH"] == (
            "${HARNESS_CODEX_CLI_PATH:-/usr/local/bin/codex}"
        )
        assert environment["HARNESS_CODEX_APPROVAL_POLICY"] == (
            "${HARNESS_CODEX_APPROVAL_POLICY:-untrusted}"
        )
        assert environment["HARNESS_CODEX_NETWORK_ACCESS"] == (
            "${HARNESS_CODEX_NETWORK_ACCESS:-false}"
        )
    assert services["worker"]["security_opt"] == ["seccomp=unconfined"]
    assert "security_opt" not in services["api"]
    assert "security_opt" not in services["quality-sync"]


def test_images_run_as_non_root_and_expose_health_checks() -> None:
    api = (ROOT / "deploy/docker/api.Dockerfile").read_text()
    web = (ROOT / "deploy/docker/web.Dockerfile").read_text()

    assert "USER harness" in api
    assert "USER nextjs" in web
    assert "HEALTHCHECK" in api
    assert "HEALTHCHECK" in web
    assert "--no-dev" in api
    assert "pypi.tuna.tsinghua.edu.cn" in api
    assert "uv export" in api
    assert "--no-emit-project" in api
    assert '.venv/bin/pip install --no-cache-dir' in api
    assert "uv sync" not in api
    assert "/usr/local/bin/codex-linux-sandbox" in api
    assert "codex-linux-sandbox --help" in api
    assert "registry.npmmirror.com" in web
    assert 'output: "standalone"' in (ROOT / "web/harness-console/next.config.ts").read_text()


def test_harbor_gray_build_reads_cache_but_pushes_one_immutable_tag() -> None:
    script = (ROOT / "scripts/build_harbor_174.sh").read_text()

    assert "docker buildx build" in script
    assert "--push" in script
    assert "--provenance=mode=max" in script
    assert "--sbom=true" in script
    assert "--provenance=false" in script
    assert "--sbom=false" in script
    assert '--cache-from "type=registry,ref=${cache_ref}"' in script
    assert '--tag "${cache_ref}"' not in script
    assert script.count('--tag "${image}"') == 1
    assert '--cache-to "type=inline"' in script
    assert "HARNESS_ALLOW_DIRTY_BUILD" in script
    assert "status --porcelain" in script
    assert '--build-arg "KUBECTL_IMAGE=${KUBECTL_IMAGE}"' in script


def test_docker_context_excludes_macos_appledouble_metadata() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert "**/._*" in dockerignore


def test_background_services_override_the_api_http_healthcheck() -> None:
    services = cast(dict[str, Any], compose()["services"])

    for name, process in (
        ("worker", "harness-worker"),
        ("quality-sync", "harness-quality-worker"),
    ):
        healthcheck = cast(dict[str, Any], services[name]["healthcheck"])
        command = cast(list[str], healthcheck["test"])
        assert command[:3] == ["CMD", "python", "-c"]
        assert process in command[3]
        assert "/healthz" not in command[3]


def test_runtime_entrypoints_and_environment_template_exist() -> None:
    api_entrypoint = ROOT / "deploy/docker/entrypoint-api.sh"
    worker_entrypoint = ROOT / "deploy/docker/entrypoint-worker.sh"
    environment = ROOT / "deploy/docker-compose/.env.docker.example"

    assert "uvicorn harness.api.app:app" in api_entrypoint.read_text()
    assert "harness-worker" in worker_entrypoint.read_text()
    assert "--unshare-user --uid 0 --gid 0 --ro-bind / / /bin/true" in (
        worker_entrypoint.read_text()
    )
    values = environment.read_text()
    assert "HARNESS_NEW_API_BASE_URL=" not in values
    assert "Settings → Model Management" in values
    assert "HARNESS_API_IMAGE_REPOSITORY=claude-agent-harness-api" in values
    assert "HARNESS_WEB_IMAGE_REPOSITORY=claude-agent-harness-web" in values
    assert "HARNESS_IMAGE_TAG=local" in values
    assert "HARNESS_API_BEARER_TOKEN=" in values
    assert "HARNESS_ALLOW_UNSAFE_LOCAL_SANDBOX=false" in values
    assert "HARNESS_SANDBOX_PROVIDER=daytona" in values
    assert "HARNESS_PREFLIGHT_TIMEOUT_SECONDS=180" in values
    assert "HARNESS_QUOTA_ENFORCEMENT_ENABLED=false" in values
    assert "HARNESS_MCP_DISCOVERY_PROXY_URL=" in values
    assert "MINIO_ROOT_PASSWORD=" in values
    assert "LANGFUSE_OTLP_ENDPOINT=" in values
    assert "LANGFUSE_EXPORT_PROXY_URL=" in values
    assert "LANGFUSE_EXPORT_NO_PROXY=" in values
    assert "LANGFUSE_PUBLIC_KEY=" in values
    assert "LANGFUSE_SECRET_KEY=" in values
    assert "LANGFUSE_ENVIRONMENT=" in values
    assert "LANGFUSE_AUTHORIZATION=" not in values
    assert "HARNESS_AGENT_NAME=lead-agent" in values
    assert "HARNESS_AGENT_VERSION=1.0.0" in values
    assert compose()["services"]["web"]["environment"]["HARNESS_AGENT_VERSION"] == (
        "${HARNESS_AGENT_VERSION:-1.0.0}"
    )
    services = cast(dict[str, Any], compose()["services"])
    for name in ("api", "seed", "web"):
        assert "HARNESS_API_BEARER_TOKEN" in services[name]["environment"]
    assert "HARNESS_API_BEARER_TOKEN" not in services["worker"]["environment"]


def test_external_langfuse_collector_uses_basic_auth() -> None:
    collector = cast(dict[str, Any], yaml.safe_load(COLLECTOR_PATH.read_text()))

    assert collector["extensions"]["basicauth/client"]["client_auth"] == {
        "username": "${env:LANGFUSE_PUBLIC_KEY}",
        "password": "${env:LANGFUSE_SECRET_KEY}",
    }
    exporter = collector["exporters"]["otlphttp/langfuse"]
    assert exporter["auth"]["authenticator"] == "basicauth/client"
    assert exporter["headers"]["x-langfuse-ingestion-version"] == "4"
    assert "Authorization" not in exporter["headers"]
    assert collector["service"]["extensions"] == ["basicauth/client"]


def test_observability_profile_scopes_external_langfuse_secrets() -> None:
    services = cast(dict[str, Any], compose()["services"])
    collector = cast(dict[str, Any], services["otel-collector"])
    environment = cast(dict[str, Any], collector["environment"])

    assert collector["profiles"] == ["observability"]
    assert environment == {
        "LANGFUSE_OTLP_ENDPOINT": "${LANGFUSE_OTLP_ENDPOINT:-}",
        "LANGFUSE_PUBLIC_KEY": "${LANGFUSE_PUBLIC_KEY:-}",
        "LANGFUSE_SECRET_KEY": "${LANGFUSE_SECRET_KEY:-}",
        "HTTP_PROXY": "${LANGFUSE_EXPORT_PROXY_URL:-}",
        "HTTPS_PROXY": "${LANGFUSE_EXPORT_PROXY_URL:-}",
        "NO_PROXY": (
            "${LANGFUSE_EXPORT_NO_PROXY:-localhost,127.0.0.1,otel-collector}"
        ),
    }
    assert collector["ports"] == [
        "127.0.0.1:${OTEL_GRPC_PORT:-4317}:4317",
        "127.0.0.1:${OTEL_HTTP_PORT:-4318}:4318",
    ]
    for name in ("api", "worker"):
        service_environment = cast(dict[str, Any], services[name]["environment"])
        assert service_environment["HARNESS_OTEL_ENVIRONMENT"] == (
            "${LANGFUSE_ENVIRONMENT:-production}"
        )
        assert "LANGFUSE_PUBLIC_KEY" not in service_environment
        assert "LANGFUSE_SECRET_KEY" not in service_environment
        assert "HARNESS_LANGFUSE_SECRET_KEY" not in service_environment
    web_environment = cast(dict[str, Any], services["web"]["environment"])
    assert web_environment["HARNESS_OTEL_ENABLED"] == "${HARNESS_OTEL_ENABLED:-false}"
    assert web_environment["HARNESS_OTEL_ENVIRONMENT"] == (
        "${LANGFUSE_ENVIRONMENT:-production}"
    )
    assert web_environment["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == (
        "http://otel-collector:4318/v1/traces"
    )
    assert web_environment["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] == "http/protobuf"
    assert "LANGFUSE_PUBLIC_KEY" not in web_environment
    assert "LANGFUSE_SECRET_KEY" not in web_environment
    quality_environment = cast(dict[str, Any], services["quality-sync"])["environment"]
    assert quality_environment["HARNESS_LANGFUSE_PUBLIC_KEY"] == (
        "${LANGFUSE_PUBLIC_KEY:?set LANGFUSE_PUBLIC_KEY}"
    )
    assert quality_environment["HARNESS_LANGFUSE_SECRET_KEY"] == (
        "${LANGFUSE_SECRET_KEY:?set LANGFUSE_SECRET_KEY}"
    )
    assert "HARNESS_NEW_API_KEY" not in quality_environment


def test_dockerignore_excludes_secrets_and_build_outputs() -> None:
    ignored = (ROOT / ".dockerignore").read_text()

    for pattern in (".env", ".git", ".venv", "node_modules", ".next", ".DS_Store"):
        assert pattern in ignored
