"""FastAPI application factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from hmac import compare_digest
from time import monotonic

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from harness.agent_package import AgentBundleValidationError, AgentPackageCheckError
from harness.agui import routes as agui_routes
from harness.api.dependencies import ApiContainer, Identity, build_memory_container
from harness.api.routes import agents, approvals, artifacts, auth, input_artifacts, runs, sessions
from harness.config import Settings
from harness.core.errors import (
    HarnessDomainError,
    NotFoundError,
    PermissionDeniedError,
    StorageCapacityError,
)
from harness.core.manifest import ManifestValidationError
from harness.governance import api as governance_routes
from harness.knowledge import api as knowledge_routes
from harness.lifecycle import api as lifecycle_routes
from harness.memory_bank import api as memory_bank_routes
from harness.platform_mcp import api as platform_mcp_routes
from harness.policy.profiles import PolicyProfileRegistry
from harness.quota.repositories import QuotaExceededError
from harness.reliability import api as reliability_routes
from harness.sharing import api as sharing_routes
from harness.sharing import groups_api as group_routes
from harness.studio import api as studio_routes
from harness.triggers import a2a as a2a_routes
from harness.triggers import api as trigger_routes


async def _http_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, HTTPException)
    detail = error.detail
    if isinstance(detail, dict) and "code" in detail:
        payload = detail
    else:
        payload = {"code": "http_error", "message": str(detail)}
    return JSONResponse(
        status_code=error.status_code,
        content={"error": payload},
        headers=error.headers,
    )


async def _request_validation_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    details = [
        {
            "type": item.get("type", "validation_error"),
            "location": list(item.get("loc", ())),
            "message": item.get("msg", "Request validation failed"),
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "request_invalid",
                "message": "Request validation failed",
                "details": details,
            }
        },
    )


async def _domain_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, HarnessDomainError)
    if isinstance(error, NotFoundError):
        status_code = 404
    elif isinstance(error, PermissionDeniedError):
        status_code = 403
    elif isinstance(error, StorageCapacityError):
        status_code = 507
    else:
        status_code = 409
    if isinstance(error, StorageCapacityError):
        code = "storage_capacity_exceeded"
    elif isinstance(error, QuotaExceededError):
        code = "quota_exceeded"
    else:
        code = (
            "not_found"
            if status_code == 404
            else "permission_denied"
            if status_code == 403
            else "conflict"
        )
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": str(error)}},
    )


async def _manifest_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ManifestValidationError)
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "manifest_invalid", "message": str(error)}},
    )


async def _agent_package_error(_request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, (AgentPackageCheckError, AgentBundleValidationError))
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "agent_package_invalid", "message": str(error)}},
    )


async def _trace_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
    container: ApiContainer = request.app.state.container
    started = monotonic()
    operation = _metric_operation(request.method, request.url.path)
    carrier = {
        name: value
        for name in ("traceparent", "tracestate", "baggage")
        if (value := request.headers.get(name))
    }
    with container.observability.span(
        "harness.api.request",
        carrier=carrier or None,
        attributes={"http.method": request.method, "http.route": request.url.path},
    ):
        try:
            response = await call_next(request)
        except BaseException:
            container.reliability_metrics.observe(
                "harness_api_request_duration_seconds",
                monotonic() - started,
                labels={"operation": operation},
            )
            if operation == "artifact.download":
                container.reliability_metrics.increment(
                    "harness_artifact_download_total",
                    labels={"outcome": "failure"},
                )
            raise
        container.reliability_metrics.observe(
            "harness_api_request_duration_seconds",
            monotonic() - started,
            labels={"operation": operation},
        )
        if operation == "artifact.download":
            container.reliability_metrics.increment(
                "harness_artifact_download_total",
                labels={"outcome": "success" if response.status_code < 400 else "failure"},
            )
        return response


def _metric_operation(method: str, path: str) -> str:
    if method == "POST" and path.endswith("/runs"):
        return "run.create"
    if method == "POST" and path.endswith("/cancel"):
        return "run.cancel"
    if method == "PUT" and "/approvals/" in path:
        return "approval.decide"
    if method == "GET" and path.endswith("/content") and "/artifacts/" in path:
        return "artifact.download"
    return "other"


async def _authenticate_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
    path = request.url.path
    protected = path.startswith("/v1") or path == "/metrics"
    if not protected or path.startswith("/v1/auth"):
        return await call_next(request)
    container: ApiContainer = request.app.state.container
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        record = await container.api_access.authenticate(api_key)
        if record is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "api_key_invalid",
                        "message": "API key is invalid or has been revoked",
                    }
                },
            )
        request.state.api_key_identity = Identity(
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            roles=frozenset(),
            authentication_method="api_key",
            permissions=frozenset(record.permissions),
        )
        return await call_next(request)
    expected = container.api_bearer_token.get_secret_value()
    if not expected:
        return await call_next(request)
    service_credential = request.headers.get("X-Harness-Service-Token", "")
    scheme, separator, credential = request.headers.get("Authorization", "").partition(" ")
    if service_credential and compare_digest(service_credential, expected):
        request.state.service_authenticated = True
        return await call_next(request)
    if separator and scheme.lower() == "bearer" and compare_digest(credential, expected):
        request.state.service_authenticated = True
        return await call_next(request)
    if separator and scheme.lower() == "bearer" and credential.count(".") == 2:
        return await call_next(request)
    if expected:
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "api_auth_required",
                    "message": "A valid Harness API credential is required",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)


async def _audit_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    identity = getattr(request.state, "identity", None)
    should_record = request.method not in {"GET", "HEAD", "OPTIONS"} or request.url.path.endswith(
        "/content"
    )
    if identity is not None and should_record:
        container: ApiContainer = request.app.state.container
        try:
            await container.audit.record(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                action=f"http.{request.method.lower()}",
                resource_type="api_route",
                resource_id=request.url.path,
                outcome="success" if response.status_code < 400 else "denied",
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details={"status_code": response.status_code},
            )
        except Exception:
            pass
    return response


async def _healthz() -> dict[str, str]:
    return {"status": "ok"}


def create_app(container: ApiContainer) -> FastAPI:
    api_token = container.api_bearer_token.get_secret_value()
    if container.environment == "production" and len(api_token) < 32:
        raise ValueError(
            "production API requires HARNESS_API_BEARER_TOKEN with at least 32 characters"
        )
    if container.environment == "production" and container.auth.jwt_secret_length < 32:
        raise ValueError("production requires HARNESS_AUTH_JWT_SECRET with at least 32 characters")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async with container.memory_mcp_app.router.lifespan_context(container.memory_mcp_app):
            async with container.knowledge_mcp_app.router.lifespan_context(
                container.knowledge_mcp_app
            ):
                async with container.platform_mcp_app.router.lifespan_context(
                    container.platform_mcp_app
                ):
                    try:
                        yield
                    finally:
                        if container.close is not None:
                            await container.close()

    app = FastAPI(
        title="Claude Agent Harness",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.container = container
    app.mount("/mcp/memory", container.memory_mcp_app)
    app.mount("/mcp/knowledge", container.knowledge_mcp_app)
    app.mount("/mcp/platform", container.platform_mcp_app)
    app.add_api_route("/healthz", _healthz, methods=["GET"], include_in_schema=False)
    app.add_api_route(
        "/metrics",
        lambda: Response(
            container.reliability_metrics.render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        ),
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(BaseHTTPMiddleware, dispatch=_trace_request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_audit_request)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_authenticate_request)

    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _request_validation_error)
    app.add_exception_handler(HarnessDomainError, _domain_error)
    app.add_exception_handler(ManifestValidationError, _manifest_error)
    app.add_exception_handler(AgentPackageCheckError, _agent_package_error)
    app.add_exception_handler(AgentBundleValidationError, _agent_package_error)
    app.add_exception_handler(
        a2a_routes.A2AProtocolError,
        a2a_routes.a2a_protocol_error_response,
    )

    for router in (
        agents.router,
        auth.router,
        sessions.router,
        runs.router,
        approvals.router,
        artifacts.router,
        input_artifacts.router,
        lifecycle_routes.router,
        memory_bank_routes.router,
        reliability_routes.router,
        sharing_routes.router,
        group_routes.router,
        agui_routes.router,
    ):
        app.include_router(router, prefix="/v1")
    app.include_router(studio_routes.router)
    app.include_router(knowledge_routes.router)
    app.include_router(governance_routes.router)
    app.include_router(trigger_routes.studio_router)
    app.include_router(trigger_routes.public_router)
    app.include_router(trigger_routes.chatops_router)
    app.include_router(a2a_routes.router)
    app.include_router(platform_mcp_routes.router)
    return app


def create_memory_app(
    *,
    auto_execute: bool = False,
    settings: Settings | None = None,
    policy_profiles: PolicyProfileRegistry | None = None,
) -> FastAPI:
    return create_app(
        build_memory_container(
            auto_execute=auto_execute,
            settings=settings,
            policy_profiles=policy_profiles,
        )
    )


def create_configured_app(settings: Settings) -> FastAPI:
    if settings.environment == "production":
        from harness.composition import build_production_container

        return create_app(build_production_container(settings, execution_enabled=False))
    return create_memory_app(
        auto_execute=settings.local_auto_execute,
        settings=settings,
    )


settings = Settings()
app = create_configured_app(settings)
