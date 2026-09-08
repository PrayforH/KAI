from datetime import UTC, datetime

import pytest

from harness.api.schemas import AgentCatalogItem
from harness.core.models import AgentVersion, AgentVersionStatus


def test_catalog_exposes_published_skill_names_without_files_or_instructions():
    version = AgentVersion(
        tenant_id="t",
        owner_user_id="u",
        name="agent",
        version="1.0.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash="a" * 64,
        created_at=datetime.now(UTC),
        snapshot={
            "skill_snapshots": [
                {
                    "name": "risk-analysis",
                    "description": "风险分析",
                    "files": [{"content": "private instructions"}],
                }
            ],
        },
    )
    item = AgentCatalogItem.from_version(version)
    assert item.model_dump()["skills"] == ({"name": "risk-analysis", "description": "风险分析"},)
    assert "private instructions" not in item.model_dump_json()
    assert AgentCatalogItem.from_version(version.model_copy(update={"snapshot": {}})).skills == ()


@pytest.mark.asyncio
async def test_catalog_refresh_keeps_skill_metadata_without_packaged_content():
    from harness.adapters.memory import InMemoryAgentRegistry

    registry = InMemoryAgentRegistry()
    version = AgentVersion(
        tenant_id="tenant",
        owner_user_id="user",
        name="lead-agent",
        version="1.0.2",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash="a" * 64,
        snapshot={
            "manifest": {},
            "files": {"large.bin": "private content"},
            "skill_snapshots": [
                {
                    "name": "skill-creator",
                    "description": "创建技能",
                    "instructions": "private body",
                    "files": [],
                }
            ],
        },
        created_at=datetime.now(UTC),
    )
    await registry.add(version)
    for _ in range(2):
        rows = await registry.list_catalog_for_user("tenant", "user")
        assert AgentCatalogItem.from_version(rows[0]).skills[0].name == "skill-creator"
        assert "files" not in rows[0].snapshot
        assert "instructions" not in rows[0].snapshot["skill_snapshots"][0]
