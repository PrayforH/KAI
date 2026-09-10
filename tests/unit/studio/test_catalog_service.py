from datetime import UTC, datetime

import pytest

from harness.core.errors import ConflictError
from harness.studio.catalog import default_capability_catalog
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.models import (
    AgentTemplate,
    CapabilityCatalogRecord,
    CapabilityRisk,
    CreateAgentDraftRequest,
    McpCapability,
    ModelRouteCapability,
    NetworkAccess,
    UpsertCatalogResourceRequest,
)
from harness.studio.repositories import InMemoryAgentDraftRepository
from harness.studio.service import AgentStudioService

NOW = datetime(2026, 7, 17, tzinfo=UTC)


def test_default_catalog_exposes_separate_deepseek_v4_routes() -> None:
    routes = {item.route_id: item for item in default_capability_catalog().model_routes}

    assert routes["deepseek-v4-flash"].models == ("deepseek-v4-flash",)
    assert routes["deepseek-v4-pro"].models == ("deepseek-v4-pro",)
    assert "new-api-default" not in routes
    assert routes["glm-5-3-flash"].models == ("glm-5.3-flash",)
    assert "anthropic-official" not in routes


def test_catalog_accepts_existing_unauthenticated_video_route() -> None:
    route = ModelRouteCapability(
        routeId="minimax-h3-video",
        label="MiniMax H3 Video",
        provider="MiniMax",
        models=("/model",),
        capabilities=("video_generation",),
        modelType="video_generation",
        baseUrl="http://video-service:8000/v1",
        apiFormat="openai_videos",
        authScheme="none",
    )

    assert route.model_type == "video_generation"
    assert route.api_format == "openai_videos"
    assert route.auth_scheme == "none"


@pytest.mark.asyncio
async def test_get_retires_anthropic_official_from_system_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog()
    retired = ModelRouteCapability(
        routeId="anthropic-official",
        label="Anthropic official",
        provider="anthropic",
        models=("claude-sonnet-4-6",),
        capabilities=("streaming", "tool_use", "tool_search"),
        credentialReference="ANTHROPIC_API_KEY",
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=8,
            catalog=catalog.model_copy(update={"model_routes": (*catalog.model_routes, retired)}),
            updatedBy="system-route-migration",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    assert upgraded.revision == 9
    assert "anthropic-official" not in {route.route_id for route in upgraded.catalog.model_routes}
    assert repeated == upgraded


@pytest.mark.asyncio
async def test_get_retires_anthropic_official_from_tenant_managed_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog()
    retired = ModelRouteCapability(
        routeId="anthropic-official",
        label="Tenant copy of retired platform route",
        provider="anthropic",
        models=("claude-sonnet-4-6",),
        capabilities=("streaming", "tool_use"),
        credentialReference="ANTHROPIC_API_KEY",
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=12,
            catalog=catalog.model_copy(update={"model_routes": (*catalog.model_routes, retired)}),
            updatedBy="tenant-admin",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    assert upgraded.revision == 13
    assert upgraded.updated_by == "tenant-admin"
    assert "anthropic-official" not in {route.route_id for route in upgraded.catalog.model_routes}
    assert repeated == upgraded


def previous_system_catalog():
    catalog = default_capability_catalog()
    return catalog.model_copy(
        update={
            "execution_profiles": tuple(
                profile
                for profile in catalog.execution_profiles
                if profile.profile_id != "local-development"
            )
        }
    )


@pytest.mark.asyncio
async def test_get_upgrades_an_untouched_system_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=1,
            catalog=previous_system_catalog(),
            updatedBy="system",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")

    assert upgraded.revision == 2
    assert upgraded.updated_by == "system-route-migration"
    assert "local-development" in {
        profile.profile_id for profile in upgraded.catalog.execution_profiles
    }


@pytest.mark.asyncio
async def test_get_migrates_legacy_daytona_default_to_docker_worker_profile() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog()
    profiles = tuple(
        profile.model_copy(
            update={
                "label": "生产隔离执行",
                "description": "在平台托管的隔离 Sandbox 中执行文件、命令和工具。",
                "sandbox_provider": "daytona",
                "risk": CapabilityRisk.MEDIUM,
                "provider_config_reference": "daytona-managed",
                "version": 1,
            }
        )
        if profile.profile_id == "isolated-default"
        else profile
        for profile in catalog.execution_profiles
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=4,
            catalog=catalog.model_copy(update={"execution_profiles": profiles}),
            updatedBy="tenant-admin",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    profile = next(
        item
        for item in upgraded.catalog.execution_profiles
        if item.profile_id == "isolated-default"
    )
    assert upgraded.revision == 5
    assert upgraded.updated_by == "tenant-admin"
    assert profile.label == "Docker 容器工作区"
    assert profile.sandbox_provider == "local"
    assert profile.provider_config_reference == "docker-worker-local"
    assert profile.production_allowed is True
    assert profile.version == 2
    assert repeated == upgraded


@pytest.mark.asyncio
async def test_get_migrates_legacy_platform_tavily_query_auth_to_bearer() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog()
    tavily = catalog.mcp_servers[0].model_copy(
        update={
            "auth_mode": "query",
            "auth_name": "tavilyApiKey",
            "version": 1,
        }
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=7,
            catalog=catalog.model_copy(update={"mcp_servers": (tavily,)}),
            updatedBy="system-route-migration",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    migrated = upgraded.catalog.mcp_servers[0]
    assert upgraded.revision == 8
    assert migrated.auth_mode == "bearer"
    assert migrated.auth_name is None
    assert migrated.version == 2
    assert repeated == upgraded


@pytest.mark.asyncio
async def test_get_never_drops_tenant_mcp_from_a_system_authored_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = previous_system_catalog()
    tenant_mcp = McpCapability(
        reference="sentiment_query_mcp",
        serverName="sentiment_query_mcp",
        label="Sentiment query",
        description="Read-only internal sentiment data.",
        endpointUrl="http://sentiment-mcp:8001/mcp",
        tools=("mcp__sentiment_query_mcp__search_risk_subjects",),
        risk=CapabilityRisk.MEDIUM,
        networkAccess=NetworkAccess.INTERNAL,
        sendsUserData=True,
        readOnly=True,
        executionLocation="external-mcp",
    )
    catalog = catalog.model_copy(update={"mcp_servers": (*catalog.mcp_servers, tenant_mcp)})
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=4,
            catalog=catalog,
            updatedBy="system",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")

    assert upgraded.revision == 5
    assert {item.reference for item in upgraded.catalog.mcp_servers} == {
        "tavily-readonly",
        "sentiment_query_mcp",
    }


@pytest.mark.asyncio
async def test_get_preserves_a_tenant_managed_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    previous = CapabilityCatalogRecord(
        tenantId="tenant-a",
        revision=7,
        catalog=previous_system_catalog(),
        updatedBy="tenant-admin",
        updatedAt=NOW,
    )
    await repository.seed(previous)
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    current = await service.get("tenant-a")

    assert current == previous
    assert "local-development" not in {
        profile.profile_id for profile in current.catalog.execution_profiles
    }


@pytest.mark.asyncio
async def test_get_adds_platform_runtime_capabilities_to_tenant_legacy_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog().model_copy(update={"runtime_capabilities": ()})
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=9,
            catalog=catalog,
            updatedBy="tenant-admin",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    assert upgraded.revision == 10
    assert upgraded.updated_by == "tenant-admin"
    assert {item.runtime for item in upgraded.catalog.runtime_capabilities} == {
        "claude-agent-sdk",
        "codex-app-server",
    }
    assert repeated == upgraded


@pytest.mark.asyncio
async def test_get_refreshes_exact_legacy_copy_in_tenant_managed_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = previous_system_catalog()
    policies = tuple(
        policy.model_copy(
            update={
                "description": "允许受控文件写入，命令和高风险动作进入审批。",
                "version": 6,
            }
        )
        if policy.policy_id == "production-standard"
        else policy
        for policy in catalog.policies
    )
    templates = tuple(
        template.model_copy(
            update={"description": "在隔离工作区中生成或修改文件，高风险操作需审批。"}
        )
        if template.template is AgentTemplate.OPERATOR
        else template
        for template in catalog.templates
    )
    previous = CapabilityCatalogRecord(
        tenantId="tenant-a",
        revision=12,
        catalog=catalog.model_copy(update={"policies": policies, "templates": templates}),
        updatedBy="tenant-admin",
        updatedAt=NOW,
    )
    await repository.seed(previous)
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    policy = next(
        item for item in upgraded.catalog.policies if item.policy_id == "production-standard"
    )
    operator = next(
        item for item in upgraded.catalog.templates if item.template is AgentTemplate.OPERATOR
    )
    assert upgraded.revision == 13
    assert upgraded.updated_by == "tenant-admin"
    assert policy.version == 7
    assert policy.description == (
        "工作区写入及策略允许的命令自动执行；高风险、越界或不确定动作拒绝或确认。"
    )
    assert operator.description == (
        "在隔离工作区中生成或修改文件；常规操作自动完成，仅在高风险边界需要确认。"
    )
    assert "local-development" not in {
        profile.profile_id for profile in upgraded.catalog.execution_profiles
    }
    assert repeated == upgraded


@pytest.mark.asyncio
async def test_get_refreshes_only_known_legacy_system_permission_copy() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog()
    policies = tuple(
        policy.model_copy(
            update={
                "description": "允许受控文件写入，命令和高风险动作进入审批。",
                "version": 4,
            }
        )
        if policy.policy_id == "production-standard"
        else policy
        for policy in catalog.policies
    )
    templates = tuple(
        template.model_copy(
            update={"description": "在隔离工作区中生成或修改文件，高风险操作需审批。"}
        )
        if template.template is AgentTemplate.OPERATOR
        else template
        for template in catalog.templates
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=8,
            catalog=catalog.model_copy(update={"policies": policies, "templates": templates}),
            updatedBy="system-profile-compatibility",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")
    repeated = await service.get("tenant-a")

    policy = next(
        item for item in upgraded.catalog.policies if item.policy_id == "production-standard"
    )
    operator = next(
        item for item in upgraded.catalog.templates if item.template is AgentTemplate.OPERATOR
    )
    assert upgraded.revision == 9
    assert upgraded.updated_by == "system-route-migration"
    assert policy.version == 5
    assert policy.description == (
        "工作区写入及策略允许的命令自动执行；高风险、越界或不确定动作拒绝或确认。"
    )
    assert operator.description == (
        "在隔离工作区中生成或修改文件；常规操作自动完成，仅在高风险边界需要确认。"
    )
    assert repeated == upgraded


@pytest.mark.asyncio
async def test_get_retires_legacy_deepseek_route_in_system_migrated_catalog() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    catalog = default_capability_catalog()
    legacy_route = ModelRouteCapability(
        routeId="new-api-default",
        label="DeepSeek V4",
        provider="deepseek",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        capabilities=("streaming", "tool_use"),
        credentialReference="CUSTOM_NEW_API_KEY",
    )
    old_catalog = catalog.model_copy(
        update={
            "model_routes": (
                legacy_route,
                *(
                    route
                    for route in catalog.model_routes
                    if route.route_id
                    not in {
                        "new-api-default",
                        "deepseek-v4-flash",
                        "deepseek-v4-pro",
                    }
                ),
            )
        }
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=24,
            catalog=old_catalog,
            updatedBy="system-profile-compatibility",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )

    upgraded = await service.get("tenant-a")

    routes = {route.route_id: route for route in upgraded.catalog.model_routes}
    assert upgraded.revision == 25
    assert upgraded.updated_by == "system-route-migration"
    assert "new-api-default" not in routes
    assert routes["deepseek-v4-flash"].models == ("deepseek-v4-flash",)
    assert routes["deepseek-v4-pro"].models == ("deepseek-v4-pro",)
    assert routes["deepseek-v4-flash"].credential_reference == "CUSTOM_NEW_API_KEY"
    assert routes["glm-5-3-flash"].models == ("glm-5.3-flash",)


@pytest.mark.asyncio
async def test_mcp_upsert_atomically_authorizes_selected_execution_profiles() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )
    catalog = default_capability_catalog()
    resource = catalog.mcp_servers[0].model_copy(
        update={
            "reference": "knowledge-search",
            "label": "Knowledge search",
            "network_access": NetworkAccess.INTERNAL,
        }
    )

    result = await service.upsert(
        tenant_id="tenant-a",
        user_id="admin-a",
        resource_type="mcp",
        resource_id="knowledge-search",
        request=UpsertCatalogResourceRequest(
            expectedRevision=1,
            resource=resource,
            allowedExecutionProfileIds=("local-development",),
        ),
    )

    profiles = {profile.profile_id: profile for profile in result.record.catalog.execution_profiles}
    assert "knowledge-search" in profiles["local-development"].allowed_mcp_references
    assert profiles["local-development"].version == 1
    assert "knowledge-search" not in profiles["isolated-default"].allowed_mcp_references
    capability = next(
        item for item in result.record.catalog.mcp_servers if item.reference == "knowledge-search"
    )
    assert capability.allowed_execution_profile_ids == ("local-development",)


@pytest.mark.asyncio
async def test_personal_mcp_capabilities_are_visible_only_to_their_owner() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )
    resource = (
        default_capability_catalog()
        .mcp_servers[0]
        .model_copy(update={"reference": "company-search", "label": "Company search"})
    )

    first = await service.upsert(
        tenant_id="tenant-a",
        user_id="user-a",
        resource_type="mcp",
        resource_id="company-search",
        request=UpsertCatalogResourceRequest(
            expectedRevision=1,
            resource=resource,
        ),
    )
    await service.upsert(
        tenant_id="tenant-a",
        user_id="user-b",
        resource_type="mcp",
        resource_id="company-search",
        request=UpsertCatalogResourceRequest(
            expectedRevision=first.record.revision,
            resource=resource.model_copy(update={"label": "My company search"}),
        ),
    )

    user_a = await service.get_for_user("tenant-a", "user-a")
    user_b = await service.get_for_user("tenant-a", "user-b")
    visible_a = {item.reference: item for item in user_a.catalog.mcp_servers}
    visible_b = {item.reference: item for item in user_b.catalog.mcp_servers}

    assert visible_a["company-search"].label == "Company search"
    assert visible_a["company-search"].owner_user_id == "user-a"
    assert visible_b["company-search"].label == "My company search"
    assert visible_b["company-search"].owner_user_id == "user-b"


@pytest.mark.asyncio
async def test_new_users_receive_platform_mcp_but_not_personal_capabilities() -> None:
    service = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )
    catalog = await service.get_for_user("tenant-a", "new-user")

    assert {item.reference for item in catalog.catalog.mcp_servers} == {
        item.reference for item in default_capability_catalog().mcp_servers
    }
    assert any(
        "tavily-readonly" in profile.allowed_mcp_references
        for profile in catalog.catalog.execution_profiles
    )


@pytest.mark.asyncio
async def test_user_can_register_a_personal_version_of_a_platform_mcp() -> None:
    service = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )
    platform = default_capability_catalog().mcp_servers[0]

    saved = await service.upsert(
        tenant_id="tenant-a",
        user_id="user-a",
        resource_type="mcp",
        resource_id=platform.reference,
        request=UpsertCatalogResourceRequest(
            expectedRevision=1,
            resource=platform,
            allowedExecutionProfileIds=("isolated-default",),
        ),
    )

    assert len(saved.record.catalog.mcp_servers) == 1
    assert saved.record.catalog.mcp_servers[0].owner_user_id == "user-a"


@pytest.mark.asyncio
async def test_legacy_custom_mcp_is_assigned_to_referencing_draft_owner() -> None:
    repository = InMemoryCapabilityCatalogRepository()
    drafts = InMemoryAgentDraftRepository()
    studio = AgentStudioService(drafts, catalog=default_capability_catalog())
    draft = await studio.create(
        tenant_id="tenant-a",
        user_id="user-a",
        request=CreateAgentDraftRequest(
            name="business-agent",
            domain="business",
            displayName="Business Agent",
            description="Uses a legacy personal MCP.",
            template=AgentTemplate.ANALYST,
        ),
    )
    await drafts.replace(
        draft.revision,
        draft.model_copy(
            update={
                "revision": draft.revision + 1,
                "spec": draft.spec.model_copy(update={"mcp_servers": ("legacy-business",)}),
            }
        ),
    )
    catalog = default_capability_catalog()
    legacy = catalog.mcp_servers[0].model_copy(
        update={"reference": "legacy-business", "label": "Legacy business"}
    )
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="tenant-a",
            revision=3,
            catalog=catalog.model_copy(update={"mcp_servers": (*catalog.mcp_servers, legacy)}),
            updatedBy="codex-deployer",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(repository, drafts, clock=lambda: NOW)

    owner = await service.get_for_user("tenant-a", "user-a")
    other = await service.get_for_user("tenant-a", "user-b")

    assert "legacy-business" in {item.reference for item in owner.catalog.mcp_servers}
    assert "legacy-business" not in {item.reference for item in other.catalog.mcp_servers}


@pytest.mark.asyncio
async def test_mcp_upsert_rejects_profile_without_required_network_access() -> None:
    service = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
        clock=lambda: NOW,
    )
    resource = (
        default_capability_catalog()
        .mcp_servers[0]
        .model_copy(
            update={
                "reference": "knowledge-search",
                "network_access": NetworkAccess.INTERNAL,
            }
        )
    )

    with pytest.raises(ConflictError, match="e2b-public-egress"):
        await service.upsert(
            tenant_id="tenant-a",
            user_id="admin-a",
            resource_type="mcp",
            resource_id="knowledge-search",
            request=UpsertCatalogResourceRequest(
                expectedRevision=1,
                resource=resource,
                allowedExecutionProfileIds=("e2b-public-egress",),
            ),
        )


@pytest.mark.asyncio
async def test_admin_edited_catalog_receives_web_tools_without_replacing_custom_entries():
    defaults = default_capability_catalog()
    existing = defaults.model_copy(
        update={
            "builtin_tools": tuple(
                tool
                for tool in defaults.builtin_tools
                if tool.name not in {"WebSearch", "WebFetch"}
            ),
        }
    )
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="admin-catalog",
            revision=5,
            catalog=existing,
            updatedBy="user-admin",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(repository, InMemoryAgentDraftRepository())
    upgraded = await service.get("admin-catalog")
    assert {"WebSearch", "WebFetch"} <= {tool.name for tool in upgraded.catalog.builtin_tools}
    assert upgraded.catalog.builtin_tools[: len(existing.builtin_tools)] == existing.builtin_tools
    assert upgraded.updated_by == "user-admin"
    assert (await service.get("admin-catalog")).revision == upgraded.revision


def test_default_catalog_seeds_platform_skill_capabilities() -> None:
    catalog = default_capability_catalog()

    assert {skill.package_id for skill in catalog.skills} >= {
        "minimax-docx",
        "minimax-xlsx",
        "pptx-generator",
        "minimax-pdf",
    }
    for skill in catalog.skills:
        assert skill.enabled is True
        assert skill.owner_user_id is None if hasattr(skill, "owner_user_id") else True
        assert skill.content_hash
        assert skill.compatible_runtimes


@pytest.mark.asyncio
async def test_admin_edited_catalog_receives_new_platform_skills() -> None:
    defaults = default_capability_catalog()
    existing = defaults.model_copy(
        update={"skills": tuple(s for s in defaults.skills if s.package_id == "evidence-reporting")}
    )
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="skill-catalog",
            revision=3,
            catalog=existing,
            updatedBy="user-admin",
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(repository, InMemoryAgentDraftRepository())
    upgraded = await service.get("skill-catalog")

    package_ids = {skill.package_id for skill in upgraded.catalog.skills}
    assert "evidence-reporting" in package_ids
    assert {"minimax-docx", "minimax-xlsx", "pptx-generator", "minimax-pdf"} <= package_ids
    # The tenant-authored entry survives untouched.
    assert upgraded.catalog.skills[0].package_id == "evidence-reporting"


@pytest.mark.asyncio
async def test_skill_disable_reports_referencing_drafts_and_upsert_is_rejected() -> None:
    from harness.studio.models import AgentDraftSpec

    defaults = default_capability_catalog()
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="skill-impact",
            revision=1,
            catalog=defaults,
            updatedBy="system",
            updatedAt=NOW,
        )
    )
    spec = AgentDraftSpec(
        name="impact-agent",
        displayName="影响面助手",
        description="目录 Skill 影响面验证。",
        domain="operations",
        template=AgentTemplate.ANALYST,
        runtime="claude-agent-sdk",
        model={"routeId": "deepseek-v4-flash", "model": "deepseek-v4-flash"},
        systemPrompt="你是测试助手。",
        permissionPolicy="production-standard",
        evaluationCases=[
            {
                "id": "baseline",
                "tags": ["baseline"],
                "prompt": "ping",
                "expect": {"terminalStatuses": ["succeeded"]},
            }
        ],
        skillReferences=("minimax-docx",),
    )
    from harness.studio.models import AgentDraft

    drafts = InMemoryAgentDraftRepository()
    await drafts.add(
        AgentDraft(
            draftId="draft-impact",
            tenantId="skill-impact",
            revision=1,
            spec=spec,
            createdBy="admin-a",
            updatedBy="admin-a",
            createdAt=NOW,
            updatedAt=NOW,
        )
    )
    service = CapabilityCatalogService(repository, drafts)

    impact = await service.impact("skill-impact", "admin-a", "skill", "minimax-docx")
    assert impact.draft_ids == ("draft-impact",)

    # Skill identity is platform-governed: the upsert payload union rejects
    # SkillCapability outright, so tenants cannot author catalog skill entries.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UpsertCatalogResourceRequest(expectedRevision=1, resource=defaults.skills[0])


@pytest.mark.asyncio
async def test_deleting_a_route_pinned_by_published_versions_is_refused() -> None:
    """A published version is immutable, so its route must survive deletion.

    On 2026-09-09 deleting the ``codex-deepseek-v4-flash`` route silently broke
    every already-published version that pinned it, because the reference check
    only looked at drafts and agent bindings.
    """

    defaults = default_capability_catalog()
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(
        CapabilityCatalogRecord(
            tenantId="route-guard",
            revision=1,
            catalog=defaults,
            updatedBy="system",
            updatedAt=NOW,
        )
    )
    published = ("public-opinion-agent@0.3.20", "public-opinion-agent@0.3.22")

    async def route_references(tenant_id: str, route_id: str) -> tuple[str, ...]:
        return published if route_id == "deepseek-v4-flash" else ()

    service = CapabilityCatalogService(
        repository,
        InMemoryAgentDraftRepository(),
        published_route_references=route_references,
    )

    impact = await service.impact("route-guard", "admin-a", "modelRoute", "deepseek-v4-flash")
    assert impact.published_agent_versions == published

    with pytest.raises(ConflictError, match="published:public-opinion-agent@0.3.22"):
        await service.delete_model(
            tenant_id="route-guard",
            user_id="admin-a",
            resource_id="deepseek-v4-flash",
            expected_revision=1,
        )

    # An unreferenced route still deletes.
    result = await service.delete_model(
        tenant_id="route-guard",
        user_id="admin-a",
        resource_id="glm-5-3-flash",
        expected_revision=1,
    )
    assert "glm-5-3-flash" not in {item.route_id for item in result.record.catalog.model_routes}
