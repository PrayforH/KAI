from types import SimpleNamespace

import pytest

from harness.core.errors import ConflictError
from harness.projects.repositories import InMemoryProjectRepository
from harness.projects.service import ProjectService


class Catalog:
    async def get_for_user(self, tenant_id, user_id):
        assert (tenant_id, user_id) == ("t", "owner")
        return SimpleNamespace(
            catalog=SimpleNamespace(
                mcp_servers=[
                    SimpleNamespace(
                        reference="data",
                        enabled=True,
                        endpoint_url="https://data.test/mcp",
                        visibility="all",
                    ),
                    SimpleNamespace(
                        reference="restricted",
                        enabled=True,
                        endpoint_url="https://data.test/mcp",
                        visibility="selected",
                    ),
                    SimpleNamespace(
                        reference="disabled",
                        enabled=False,
                        endpoint_url="https://data.test/mcp",
                        visibility="all",
                    ),
                ]
            )
        )


@pytest.mark.asyncio
async def test_project_connectors_are_scoped_validated_and_captured_per_run():
    service = ProjectService(InMemoryProjectRepository(), connector_catalog=Catalog())
    project = await service.create(tenant_id="t", user_id="owner", name="Review")
    await service.update(
        tenant_id="t", user_id="owner", project_id=project.project_id, mcp_references=("data",)
    )
    captured = await service.context_for_run("t", "owner", project.project_id)
    assert captured.mcp_references == ("data",)
    assert captured.resource_manifest["mcpReferences"] == ["data"]
    for refs in [("restricted",), ("disabled",), ("missing",), ("data", "data")]:
        with pytest.raises(ConflictError):
            await service.update(
                tenant_id="t", user_id="owner", project_id=project.project_id, mcp_references=refs
            )
    await service.update(
        tenant_id="t", user_id="owner", project_id=project.project_id, mcp_references=()
    )
    assert captured.mcp_references == ("data",)
    assert (await service.context_for_run("t", "owner", project.project_id)).mcp_references == ()
