"""Catalog seeding, optimistic updates and Draft impact analysis."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

from harness.core.errors import ConflictError, NotFoundError
from harness.studio.catalog import default_capability_catalog
from harness.studio.catalog_repository import CapabilityCatalogRepository
from harness.studio.models import (
    BuiltinToolCapability,
    CapabilityCatalog,
    CapabilityCatalogRecord,
    CatalogImpact,
    CatalogMutationResult,
    ExecutionProfileMetadata,
    McpCapability,
    ModelRouteCapability,
    PolicyCapability,
    ReplaceCapabilityCatalogRequest,
    RuntimeCapability,
    SkillCapability,
    TemplateCapability,
    UpsertCatalogResourceRequest,
)
from harness.studio.repositories import AgentDraftRepository

CatalogResourceType = Literal["modelRoute", "mcp", "policy", "executionProfile", "skill"]
_RETIRED_PLATFORM_MODEL_ROUTES = frozenset({"anthropic-official", "new-api-default"})
_EDITABLE_PLATFORM_MCP_REFERENCES = frozenset({"tavily-readonly"})


def _mcp_is_mutable_by(item: McpCapability, user_id: str) -> bool:
    return item.owner_user_id == user_id or (
        item.owner_user_id is None and item.reference in _EDITABLE_PLATFORM_MCP_REFERENCES
    )


def _retire_platform_model_routes(
    catalog: CapabilityCatalog,
) -> CapabilityCatalog | None:
    routes = tuple(
        route
        for route in catalog.model_routes
        if route.route_id not in _RETIRED_PLATFORM_MODEL_ROUTES
    )
    if len(routes) == len(catalog.model_routes):
        return None
    return catalog.model_copy(update={"model_routes": routes})


def _append_missing[
    CatalogEntry: (
        ModelRouteCapability,
        BuiltinToolCapability,
        McpCapability,
        PolicyCapability,
        ExecutionProfileMetadata,
        TemplateCapability,
        RuntimeCapability,
        SkillCapability,
    )
](
    current: tuple[CatalogEntry, ...],
    builtins: tuple[CatalogEntry, ...],
    identifier: Callable[[CatalogEntry], str],
) -> tuple[tuple[CatalogEntry, ...], bool]:
    identifiers = {identifier(item) for item in current}
    additions = tuple(item for item in builtins if identifier(item) not in identifiers)
    return (*current, *additions), bool(additions)


def _upgrade_known_legacy_permission_copy(
    catalog: CapabilityCatalog,
) -> CapabilityCatalog | None:
    """Refresh exact historical defaults without touching tenant-authored copy."""

    defaults = default_capability_catalog()
    default_policies = {item.policy_id: item for item in defaults.policies}
    policies: list[PolicyCapability] = []
    changed = False
    for policy in catalog.policies:
        replacement = default_policies.get(policy.policy_id)
        if (
            replacement is not None
            and policy.policy_id == "production-standard"
            and policy.description == "允许受控文件写入，命令和高风险动作进入审批。"
        ):
            policies.append(
                policy.model_copy(
                    update={
                        "description": replacement.description,
                        "version": policy.version + 1,
                    }
                )
            )
            changed = True
        else:
            policies.append(policy)

    default_templates = {item.template: item for item in defaults.templates}
    templates: list[TemplateCapability] = []
    for template in catalog.templates:
        replacement = default_templates.get(template.template)
        if (
            replacement is not None
            and template.description == "在隔离工作区中生成或修改文件，高风险操作需审批。"
        ):
            templates.append(template.model_copy(update={"description": replacement.description}))
            changed = True
        else:
            templates.append(template)

    default_mcp_servers = {item.reference: item for item in defaults.mcp_servers}
    mcp_servers: list[McpCapability] = []
    for mcp in catalog.mcp_servers:
        replacement = default_mcp_servers.get(mcp.reference)
        if (
            replacement is not None
            and mcp.reference == "tavily-readonly"
            and mcp.owner_user_id is None
            and mcp.auth_mode == "query"
            and mcp.auth_name == "tavilyApiKey"
            and mcp.auth_key == "api_key"
        ):
            mcp_servers.append(
                mcp.model_copy(
                    update={
                        "auth_mode": replacement.auth_mode,
                        "auth_name": replacement.auth_name,
                        "version": max(mcp.version + 1, replacement.version),
                    }
                )
            )
            changed = True
        else:
            mcp_servers.append(mcp)

    default_profiles = {item.profile_id: item for item in defaults.execution_profiles}
    execution_profiles: list[ExecutionProfileMetadata] = []
    for profile in catalog.execution_profiles:
        replacement = default_profiles.get(profile.profile_id)
        if (
            replacement is not None
            and profile.profile_id == "isolated-default"
            and profile.label == "生产隔离执行"
            and profile.sandbox_provider == "daytona"
            and profile.provider_config_reference == "daytona-managed"
        ):
            execution_profiles.append(
                profile.model_copy(
                    update={
                        "label": replacement.label,
                        "description": replacement.description,
                        "sandbox_provider": replacement.sandbox_provider,
                        "risk": replacement.risk,
                        "provider_config_reference": replacement.provider_config_reference,
                        "production_allowed": replacement.production_allowed,
                        "version": max(profile.version + 1, replacement.version),
                    }
                )
            )
            changed = True
        else:
            execution_profiles.append(profile)

    if not changed:
        return None
    return catalog.model_copy(
        update={
            "policies": tuple(policies),
            "templates": tuple(templates),
            "mcp_servers": tuple(mcp_servers),
            "execution_profiles": tuple(execution_profiles),
        }
    )


def _upgrade_system_managed_catalog(
    catalog: CapabilityCatalog,
) -> CapabilityCatalog | None:
    """Upgrade known system defaults without dropping tenant-authored entries."""

    defaults = default_capability_catalog()
    legacy_deepseek = next(
        (route for route in catalog.model_routes if route.route_id == "new-api-default"),
        None,
    )
    routes = [
        route
        for route in catalog.model_routes
        if route.route_id not in _RETIRED_PLATFORM_MODEL_ROUTES
    ]
    changed = len(routes) != len(catalog.model_routes)
    normalized_routes: list[ModelRouteCapability] = []
    for route in routes:
        if "vision" in route.capabilities and route.model_type == "chat":
            normalized_routes.append(route.model_copy(update={"model_type": "vision"}))
            changed = True
        else:
            normalized_routes.append(route)
    routes = normalized_routes
    route_ids = {route.route_id for route in routes}
    if legacy_deepseek is not None and not {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    }.issubset(route_ids):
        routes.extend(
            ModelRouteCapability(
                routeId=route_id,
                label=label,
                provider=legacy_deepseek.provider,
                models=(model,),
                capabilities=legacy_deepseek.capabilities,
                credentialManaged=legacy_deepseek.credential_managed,
                credentialReference=legacy_deepseek.credential_reference,
            )
            for route_id, label, model in (
                (
                    "deepseek-v4-flash",
                    "DeepSeek V4 Flash",
                    "deepseek-v4-flash",
                ),
                ("deepseek-v4-pro", "DeepSeek V4 Pro", "deepseek-v4-pro"),
            )
            if route_id not in route_ids
        )
        changed = True

    builtin_tools, builtin_changed = _append_missing(
        catalog.builtin_tools, defaults.builtin_tools, lambda item: item.name
    )
    mcp_servers, mcp_changed = _append_missing(
        catalog.mcp_servers, defaults.mcp_servers, lambda item: item.reference
    )
    policies, policies_changed = _append_missing(
        catalog.policies, defaults.policies, lambda item: item.policy_id
    )
    execution_profiles, profiles_changed = _append_missing(
        catalog.execution_profiles,
        defaults.execution_profiles,
        lambda item: item.profile_id,
    )
    templates, templates_changed = _append_missing(
        catalog.templates,
        defaults.templates,
        lambda item: item.template.value,
    )
    runtime_capabilities, runtime_capabilities_changed = _append_missing(
        catalog.runtime_capabilities,
        defaults.runtime_capabilities,
        lambda item: item.runtime,
    )
    skills, skills_changed = _append_missing(
        catalog.skills,
        defaults.skills,
        lambda item: item.package_id,
    )
    changed = changed or any(
        (
            builtin_changed,
            mcp_changed,
            policies_changed,
            profiles_changed,
            templates_changed,
            runtime_capabilities_changed,
            skills_changed,
        )
    )

    upgraded = catalog.model_copy(
        update={
            "model_routes": routes,
            "builtin_tools": builtin_tools,
            "mcp_servers": mcp_servers,
            "policies": policies,
            "execution_profiles": execution_profiles,
            "templates": templates,
            "runtime_capabilities": runtime_capabilities,
            "skills": skills,
        }
    )
    permission_copy_upgrade = _upgrade_known_legacy_permission_copy(upgraded)
    return permission_copy_upgrade or (upgraded if changed else None)


class CapabilityCatalogService:
    def __init__(
        self,
        repository: CapabilityCatalogRepository,
        drafts: AgentDraftRepository,
        *,
        published_route_references: Callable[[str, str], Awaitable[tuple[str, ...]]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._drafts = drafts
        # Published Agent versions are immutable and sessions pin them, so a
        # route removal must see them; drafts alone are not the whole impact.
        self._published_route_references = published_route_references
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _published_versions_for_route(self, tenant_id: str, route_id: str) -> tuple[str, ...]:
        if self._published_route_references is None:
            return ()
        return await self._published_route_references(tenant_id, route_id)

    async def get(self, tenant_id: str) -> CapabilityCatalogRecord:
        seed = CapabilityCatalogRecord(
            tenantId=tenant_id,
            revision=1,
            catalog=default_capability_catalog(),
            updatedBy="system",
            updatedAt=self._clock(),
        )
        current = await self._repository.seed(seed)
        if current.updated_by == "system" or current.updated_by.startswith("system-"):
            # The system migration needs to inspect retired grouped routes before
            # removing them so it can preserve their credentials on split routes.
            upgraded_catalog = _upgrade_system_managed_catalog(current.catalog)
            updated_by = "system-route-migration"
        else:
            retired_catalog = _retire_platform_model_routes(current.catalog)
            catalog_for_upgrade = retired_catalog or current.catalog
            upgraded_catalog = (
                _upgrade_known_legacy_permission_copy(catalog_for_upgrade) or retired_catalog
            )
            catalog_for_runtime_upgrade = upgraded_catalog or current.catalog
            runtime_capabilities, runtime_capabilities_changed = _append_missing(
                catalog_for_runtime_upgrade.runtime_capabilities,
                default_capability_catalog().runtime_capabilities,
                lambda item: item.runtime,
            )
            if runtime_capabilities_changed:
                upgraded_catalog = catalog_for_runtime_upgrade.model_copy(
                    update={"runtime_capabilities": runtime_capabilities}
                )
            # Platform Skills are platform-governed (tenants can only disable
            # them), so new packages also reach admin-edited catalogs without
            # touching tenant entries.
            catalog_for_skills = upgraded_catalog or current.catalog
            catalog_skills, catalog_skills_changed = _append_missing(
                catalog_for_skills.skills,
                default_capability_catalog().skills,
                lambda item: item.package_id,
            )
            if catalog_skills_changed:
                upgraded_catalog = catalog_for_skills.model_copy(update={"skills": catalog_skills})
            updated_by = current.updated_by
        # Platform web tools are selectable capabilities, not automatic grants.
        # Also expose them in admin-edited catalogs without replacing custom entries.
        catalog_for_web = upgraded_catalog or current.catalog
        web_tools, web_changed = _append_missing(
            catalog_for_web.builtin_tools,
            tuple(
                item
                for item in default_capability_catalog().builtin_tools
                if item.name in {"WebSearch", "WebFetch"}
            ),
            lambda item: item.name,
        )
        if web_changed:
            upgraded_catalog = catalog_for_web.model_copy(update={"builtin_tools": web_tools})
        catalog_for_scope = upgraded_catalog or current.catalog
        scoped_catalog = await self._scope_legacy_mcp_capabilities(
            tenant_id,
            catalog_for_scope,
        )
        if scoped_catalog is not None:
            upgraded_catalog = scoped_catalog
            updated_by = "system-user-resource-scope-migration"
        if upgraded_catalog is None or current.catalog == upgraded_catalog:
            return current
        upgraded = CapabilityCatalogRecord(
            tenantId=tenant_id,
            revision=current.revision + 1,
            catalog=upgraded_catalog,
            updatedBy=updated_by,
            updatedAt=self._clock(),
        )
        try:
            await self._repository.replace(current.revision, upgraded)
            return upgraded
        except ConflictError:
            # A tenant admin may have replaced the catalog after our read. Never
            # overwrite that concurrent decision with built-in defaults.
            return await self._repository.get(tenant_id)

    async def get_for_user(
        self,
        tenant_id: str,
        user_id: str,
    ) -> CapabilityCatalogRecord:
        """Return platform controls plus only the current user's personal MCP entries."""

        return self._record_for_user(await self.get(tenant_id), user_id)

    async def _scope_legacy_mcp_capabilities(
        self,
        tenant_id: str,
        catalog: CapabilityCatalog,
    ) -> CapabilityCatalog | None:
        platform_references = {item.reference for item in default_capability_catalog().mcp_servers}
        legacy = tuple(
            item
            for item in catalog.mcp_servers
            if item.owner_user_id is None and item.reference not in platform_references
        )
        if not legacy:
            return None
        owners_by_reference: dict[str, set[str]] = {}
        for draft in await self._drafts.list_all_for_tenant(tenant_id):
            for reference in draft.spec.mcp_servers:
                owners_by_reference.setdefault(reference, set()).add(draft.created_by)
        scoped: list[McpCapability] = []
        for capability in catalog.mcp_servers:
            if capability not in legacy:
                scoped.append(capability)
                continue
            owners = sorted(owners_by_reference.get(capability.reference, ()))
            if not owners:
                owners = ["legacy-unassigned"]
            allowed_profile_ids = tuple(
                profile.profile_id
                for profile in catalog.execution_profiles
                if capability.reference in profile.allowed_mcp_references
            )
            scoped.extend(
                capability.model_copy(
                    update={
                        "owner_user_id": owner,
                        "allowed_execution_profile_ids": allowed_profile_ids,
                    }
                )
                for owner in owners
            )
        return catalog.model_copy(update={"mcp_servers": tuple(scoped)})

    @staticmethod
    def _record_for_user(
        record: CapabilityCatalogRecord,
        user_id: str,
    ) -> CapabilityCatalogRecord:
        visible_model_routes = tuple(
            # Endpoint details remain visible only through the administrator
            # model-management API. Runtime users select logical route IDs.
            item.model_copy(update={"base_url": None})
            for item in record.catalog.model_routes
        )
        personal = tuple(
            item for item in record.catalog.mcp_servers if item.owner_user_id == user_id
        )
        personal_overrides = {item.reference for item in personal}
        platform = tuple(
            item
            for item in record.catalog.mcp_servers
            if item.owner_user_id is None and item.reference not in personal_overrides
        )
        visible = (*platform, *personal)
        personal_references = {
            item.reference for item in record.catalog.mcp_servers if item.owner_user_id is not None
        }
        platform_mcp_references = {
            item.reference for item in record.catalog.mcp_servers if item.owner_user_id is None
        }
        personal_by_profile: dict[str, list[str]] = {}
        for item in visible:
            if item.owner_user_id != user_id:
                continue
            for profile_id in item.allowed_execution_profile_ids:
                personal_by_profile.setdefault(profile_id, []).append(item.reference)
        execution_profiles = tuple(
            profile.model_copy(
                update={
                    "allowed_mcp_references": tuple(
                        dict.fromkeys(
                            (
                                *(
                                    reference
                                    for reference in profile.allowed_mcp_references
                                    if (
                                        reference in platform_mcp_references
                                        and reference not in personal_overrides
                                    )
                                    or reference not in personal_references
                                ),
                                *personal_by_profile.get(profile.profile_id, ()),
                            )
                        )
                    )
                }
            )
            for profile in record.catalog.execution_profiles
        )
        return record.model_copy(
            update={
                "updated_by": "personal-catalog",
                "catalog": record.catalog.model_copy(
                    update={
                        "model_routes": visible_model_routes,
                        "mcp_servers": visible,
                        "execution_profiles": execution_profiles,
                    }
                ),
            }
        )

    async def replace(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: ReplaceCapabilityCatalogRequest,
    ) -> CapabilityCatalogRecord:
        current = await self.get(tenant_id)
        updated = CapabilityCatalogRecord(
            tenantId=tenant_id,
            revision=current.revision + 1,
            catalog=request.catalog,
            updatedBy=user_id,
            updatedAt=self._clock(),
        )
        await self._repository.replace(request.expected_revision, updated)
        return updated

    async def impact(
        self,
        tenant_id: str,
        user_id: str,
        resource_type: CatalogResourceType,
        resource_id: str,
    ) -> CatalogImpact:
        catalog = (await self.get_for_user(tenant_id, user_id)).catalog
        if not self._contains(catalog, resource_type, resource_id):
            raise NotFoundError(f"Catalog resource not found: {resource_type}/{resource_id}")
        published = (
            await self._published_versions_for_route(tenant_id, resource_id)
            if resource_type == "modelRoute"
            else ()
        )
        affected: list[str] = []
        drafts = (
            await self._drafts.list_for_user(tenant_id, user_id)
            if resource_type == "mcp"
            else await self._drafts.list_all_for_tenant(tenant_id)
        )
        for draft in drafts:
            spec = draft.spec
            referenced = (resource_type == "modelRoute" and spec.model.route_id == resource_id) or (
                resource_type == "mcp" and resource_id in spec.mcp_servers
            )
            referenced = referenced or (
                resource_type == "policy" and spec.permission_policy == resource_id
            )
            referenced = referenced or (
                resource_type == "executionProfile" and spec.execution_profile == resource_id
            )
            referenced = referenced or (
                resource_type == "skill" and resource_id in spec.skill_references
            )
            if referenced:
                affected.append(draft.draft_id)
        return CatalogImpact(
            resourceType=resource_type,
            resourceId=resource_id,
            draftIds=tuple(sorted(affected)),
            publishedAgentVersions=published,
        )

    async def disable(
        self,
        *,
        tenant_id: str,
        user_id: str,
        resource_type: CatalogResourceType,
        resource_id: str,
        expected_revision: int,
    ) -> CatalogMutationResult:
        current = await self.get(tenant_id)
        if resource_type == "mcp":
            own_entry = next(
                (
                    item
                    for item in current.catalog.mcp_servers
                    if item.reference == resource_id and _mcp_is_mutable_by(item, user_id)
                ),
                None,
            )
            if own_entry is None:
                raise NotFoundError(f"Catalog resource not found: mcp/{resource_id}")
        impact = await self.impact(tenant_id, user_id, resource_type, resource_id)
        field = {
            "modelRoute": "model_routes",
            "mcp": "mcp_servers",
            "policy": "policies",
            "executionProfile": "execution_profiles",
            "skill": "skills",
        }[resource_type]
        identifier = {
            "modelRoute": "route_id",
            "mcp": "reference",
            "policy": "policy_id",
            "executionProfile": "profile_id",
            "skill": "package_id",
        }[resource_type]
        entries = getattr(current.catalog, field)
        updated_entries = tuple(
            entry.model_copy(update={"enabled": False, "version": entry.version + 1})
            if getattr(entry, identifier) == resource_id
            and (resource_type != "mcp" or _mcp_is_mutable_by(entry, user_id))
            else entry
            for entry in entries
        )
        updated_catalog = current.catalog.model_copy(update={field: updated_entries})
        record = await self.replace(
            tenant_id=tenant_id,
            user_id=user_id,
            request=ReplaceCapabilityCatalogRequest(
                expectedRevision=expected_revision,
                catalog=updated_catalog,
            ),
        )
        return CatalogMutationResult(
            record=self._record_for_user(record, user_id),
            impact=impact,
        )

    async def delete_mcp(
        self,
        *,
        tenant_id: str,
        user_id: str,
        resource_id: str,
        expected_revision: int,
    ) -> CatalogMutationResult:
        """Permanently remove an unreferenced user-managed MCP connection."""

        current = await self.get(tenant_id)
        own_entry = next(
            (
                item
                for item in current.catalog.mcp_servers
                if item.reference == resource_id and _mcp_is_mutable_by(item, user_id)
            ),
            None,
        )
        if own_entry is None:
            raise NotFoundError(f"Catalog resource not found: mcp/{resource_id}")
        impact = await self.impact(tenant_id, user_id, "mcp", resource_id)
        if impact.draft_ids:
            raise ConflictError(
                "Remove this capability from the affected agent drafts before deleting it: "
                + ", ".join(impact.draft_ids)
            )
        updated_catalog = current.catalog.model_copy(
            update={
                "mcp_servers": tuple(
                    item
                    for item in current.catalog.mcp_servers
                    if not (item.reference == resource_id and _mcp_is_mutable_by(item, user_id))
                )
            }
        )
        record = await self.replace(
            tenant_id=tenant_id,
            user_id=user_id,
            request=ReplaceCapabilityCatalogRequest(
                expectedRevision=expected_revision,
                catalog=updated_catalog,
            ),
        )
        return CatalogMutationResult(
            record=self._record_for_user(record, user_id),
            impact=impact,
        )

    async def delete_model(
        self,
        *,
        tenant_id: str,
        user_id: str,
        resource_id: str,
        expected_revision: int,
    ) -> CatalogMutationResult:
        """Permanently remove an unreferenced workspace model route."""

        current = await self.get(tenant_id)
        if not any(item.route_id == resource_id for item in current.catalog.model_routes):
            raise NotFoundError(f"Catalog resource not found: modelRoute/{resource_id}")
        impact = await self.impact(tenant_id, user_id, "modelRoute", resource_id)
        bound_agents = tuple(
            sorted(
                agent_name
                for agent_name, route_id in current.catalog.agent_model_bindings.items()
                if route_id == resource_id
            )
        )
        if impact.draft_ids or bound_agents or impact.published_agent_versions:
            references = (
                *(f"draft:{draft_id}" for draft_id in impact.draft_ids),
                *(f"agent:{agent_name}" for agent_name in bound_agents),
                *(f"published:{coordinate}" for coordinate in impact.published_agent_versions),
            )
            raise ConflictError(
                "Rebind or update these references before deleting the model: "
                + ", ".join(references)
            )
        updated_catalog = current.catalog.model_copy(
            update={
                "model_routes": tuple(
                    item for item in current.catalog.model_routes if item.route_id != resource_id
                )
            }
        )
        record = await self.replace(
            tenant_id=tenant_id,
            user_id=user_id,
            request=ReplaceCapabilityCatalogRequest(
                expectedRevision=expected_revision,
                catalog=updated_catalog,
            ),
        )
        return CatalogMutationResult(
            record=self._record_for_user(record, user_id),
            impact=impact,
        )

    async def upsert(
        self,
        *,
        tenant_id: str,
        user_id: str,
        resource_type: CatalogResourceType,
        resource_id: str,
        request: UpsertCatalogResourceRequest,
    ) -> CatalogMutationResult:
        type_matches = (
            resource_type == "modelRoute" and isinstance(request.resource, ModelRouteCapability)
        ) or (resource_type == "mcp" and isinstance(request.resource, McpCapability))
        type_matches = type_matches or (
            resource_type == "policy" and isinstance(request.resource, PolicyCapability)
        )
        type_matches = type_matches or (
            resource_type == "executionProfile"
            and isinstance(request.resource, ExecutionProfileMetadata)
        )
        if resource_type == "skill":
            # Platform Skill identity is governed by the platform package
            # catalog; tenants may only toggle availability via disable().
            raise ConflictError(
                "Platform Skills are governed by the platform catalog; "
                "use the disable operation to toggle availability"
            )
        if not type_matches:
            raise ConflictError(f"Catalog resource type mismatch: expected {resource_type}")
        field = {
            "modelRoute": "model_routes",
            "mcp": "mcp_servers",
            "policy": "policies",
            "executionProfile": "execution_profiles",
            "skill": "skills",
        }[resource_type]
        identifier = {
            "modelRoute": "route_id",
            "mcp": "reference",
            "policy": "policy_id",
            "executionProfile": "profile_id",
            "skill": "package_id",
        }[resource_type]
        if getattr(request.resource, identifier) != resource_id:
            raise ConflictError("Catalog resource path and body IDs must match")
        current = await self.get(tenant_id)
        entries = getattr(current.catalog, field)
        if resource_type == "mcp":
            existing = next(
                (
                    entry
                    for entry in entries
                    if entry.reference == resource_id and entry.owner_user_id == user_id
                ),
                None,
            )
        else:
            existing = next(
                (entry for entry in entries if getattr(entry, identifier) == resource_id),
                None,
            )
        impact = (
            await self.impact(tenant_id, user_id, resource_type, resource_id)
            if existing is not None
            else CatalogImpact(
                resourceType=resource_type,
                resourceId=resource_id,
                draftIds=(),
            )
        )
        resource_updates: dict[str, object] = {
            "version": 1 if existing is None else existing.version + 1
        }
        if resource_type == "mcp":
            resource_updates["owner_user_id"] = user_id
        if resource_type == "mcp" and request.allowed_execution_profile_ids is not None:
            if not isinstance(request.resource, McpCapability):
                raise ConflictError("Catalog resource type does not match MCP payload")
            mcp_resource = request.resource
            selected_profile_ids = set(request.allowed_execution_profile_ids)
            profiles_by_id = {
                profile.profile_id: profile for profile in current.catalog.execution_profiles
            }
            unknown_profile_ids = selected_profile_ids.difference(profiles_by_id)
            if unknown_profile_ids:
                raise ConflictError(
                    "Unknown Execution Profiles: " + ", ".join(sorted(unknown_profile_ids))
                )
            unavailable_profile_ids = {
                profile_id
                for profile_id in selected_profile_ids
                if not profiles_by_id[profile_id].enabled
            }
            if unavailable_profile_ids:
                raise ConflictError(
                    "Disabled Execution Profiles cannot be authorized: "
                    + ", ".join(sorted(unavailable_profile_ids))
                )
            incompatible_profile_ids = {
                profile_id
                for profile_id in selected_profile_ids
                if mcp_resource.network_access not in profiles_by_id[profile_id].network_access
            }
            if incompatible_profile_ids:
                raise ConflictError(
                    f"MCP network access {mcp_resource.network_access.value} "
                    "is not supported by "
                    "Execution Profiles: " + ", ".join(sorted(incompatible_profile_ids))
                )
            resource_updates["allowed_execution_profile_ids"] = tuple(
                request.allowed_execution_profile_ids
            )
        elif resource_type == "mcp" and isinstance(existing, McpCapability):
            resource_updates["allowed_execution_profile_ids"] = (
                existing.allowed_execution_profile_ids
            )
        resource = request.resource.model_copy(update=resource_updates)
        updated_entries = tuple(
            resource
            if getattr(entry, identifier) == resource_id
            and (resource_type != "mcp" or entry.owner_user_id == user_id)
            else entry
            for entry in entries
        )
        if existing is None:
            updated_entries = (*updated_entries, resource)
        catalog = current.catalog.model_copy(update={field: updated_entries})
        record = await self.replace(
            tenant_id=tenant_id,
            user_id=user_id,
            request=ReplaceCapabilityCatalogRequest(
                expectedRevision=request.expected_revision,
                catalog=catalog,
            ),
        )
        return CatalogMutationResult(
            record=self._record_for_user(record, user_id),
            impact=impact,
        )

    @staticmethod
    def _contains(
        catalog: CapabilityCatalog,
        resource_type: CatalogResourceType,
        resource_id: str,
    ) -> bool:
        identifiers = {
            "modelRoute": {item.route_id for item in catalog.model_routes},
            "mcp": {item.reference for item in catalog.mcp_servers},
            "policy": {item.policy_id for item in catalog.policies},
            "executionProfile": {item.profile_id for item in catalog.execution_profiles},
            "skill": {item.package_id for item in catalog.skills},
        }
        return resource_id in identifiers[resource_type]
