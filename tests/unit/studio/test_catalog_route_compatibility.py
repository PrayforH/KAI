"""Catalog refresh must not break persisted assistant model selections."""

from datetime import UTC, datetime

import pytest

from harness.studio.catalog import default_capability_catalog
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.models import CapabilityCatalogRecord, ModelRouteCapability
from harness.studio.repositories import InMemoryAgentDraftRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("updated_by", ["system-route-migration", "tenant-admin"])
@pytest.mark.parametrize("route_id", ["deepseek-v4-flash", "anthropic-official"])
async def test_refresh_preserves_bound_route_credentials_and_agent_selection(
    updated_by: str, route_id: str,
) -> None:
    route = ModelRouteCapability(
        routeId=route_id, label="Existing configured route", provider="gateway",
        models=("configured-model",), capabilities=("streaming", "tool_use"),
        credentialReference="EXISTING_PRIVATE_KEY_REFERENCE",
    )
    defaults = default_capability_catalog()
    catalog = defaults.model_copy(update={
        "model_routes": (
            *(r for r in defaults.model_routes if r.route_id != route_id), route,
        ),
        "agent_model_bindings": {"lead-agent": route_id},
    })
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(CapabilityCatalogRecord(
        tenantId="fixture", revision=7, catalog=catalog, updatedBy=updated_by,
        updatedAt=datetime(2026, 11, 4, tzinfo=UTC),
    ))
    service = CapabilityCatalogService(repository, InMemoryAgentDraftRepository())
    result = await service.get("fixture")
    assert result.catalog.agent_model_bindings == {"lead-agent": route_id}
    assert next(r for r in result.catalog.model_routes if r.route_id == route_id) == route
    assert await service.get("fixture") == result
    CapabilityCatalogRecord.model_validate(result.model_dump())


@pytest.mark.asyncio
async def test_legacy_route_split_keeps_credentials_without_replacing_existing_routes() -> None:
    defaults = default_capability_catalog()
    legacy = ModelRouteCapability(
        routeId="new-api-default", label="Existing grouped route", provider="gateway",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        capabilities=("streaming", "tool_use"), credentialReference="EXISTING_KEY",
    )
    repository = InMemoryCapabilityCatalogRepository()
    await repository.seed(CapabilityCatalogRecord(
        tenantId="fixture", revision=3,
        catalog=defaults.model_copy(update={"model_routes": (*defaults.model_routes, legacy)}),
        updatedBy="system-route-migration", updatedAt=datetime(2026, 11, 4, tzinfo=UTC),
    ))
    service = CapabilityCatalogService(repository, InMemoryAgentDraftRepository())
    result = await service.get("fixture")
    routes = {r.route_id: r for r in result.catalog.model_routes}
    assert "new-api-default" not in routes
    for r in defaults.model_routes:
        assert routes[r.route_id] == r
    assert routes["deepseek-v4-flash"].credential_reference == "EXISTING_KEY"
    assert routes["deepseek-v4-pro"].credential_reference == "EXISTING_KEY"
