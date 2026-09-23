from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from harness.adapters.memory import InMemoryArtifactStore
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.storage.models import AgentDraftRevisionRow, AgentDraftRow, AgentVersionRow
from harness.storage.platform_repositories import PostgresAgentRegistry
from harness.storage.skill_blobs import SkillBlobStore
from harness.storage.studio_repository import PostgresAgentDraftRepository
from harness.studio.models import DraftSkill, DraftSkillFile
from tests.contracts.agent_draft_repository import draft, exercise_repository_contract


@pytest.mark.asyncio
async def test_reference_repository_contract(database):
    _, sessions = database
    await exercise_repository_contract(
        PostgresAgentDraftRepository(sessions, SkillBlobStore(InMemoryArtifactStore())),
    )


@pytest.mark.asyncio
async def test_legacy_revision_and_version_roundtrip_without_blob_reads_in_lists(database):
    _, sessions = database
    store = InMemoryArtifactStore()
    original = draft()
    skill = DraftSkill(
        name="report",
        description="Report",
        instructions="Instructions",
        files=(DraftSkillFile(path="asset.bin", contentBase64="AA=="),),
    )
    original = original.model_copy(
        update={"spec": original.spec.model_copy(update={"skills": (skill,)})}
    )
    legacy = PostgresAgentDraftRepository(sessions)
    await legacy.add(original)
    repo = PostgresAgentDraftRepository(sessions, SkillBlobStore(store))
    assert await repo.get("tenant-a", "builder-a", original.draft_id) == original
    updated = original.model_copy(update={"revision": 2})
    await repo.replace(1, updated)
    with pytest.raises(ConflictError):
        await repo.replace(1, updated)
    with pytest.raises(NotFoundError):
        await repo.get("tenant-a", "other-owner", original.draft_id)
    async with sessions() as db:
        current = (await db.scalars(select(AgentDraftRow))).one()
        archived = (await db.scalars(select(AgentDraftRevisionRow))).one()
        reference = current.payload["spec"]["skills"][0]
        assert "files" not in reference and "instructions" not in reference
        assert archived.payload["spec"]["skills"][0] == reference
    restarted = PostgresAgentDraftRepository(sessions, SkillBlobStore(store))
    assert await restarted.get_revision("tenant-a", "builder-a", original.draft_id, 1) == original
    assert await restarted.get("tenant-a", "builder-a", original.draft_id) == updated
    assert (await restarted.list_summaries("tenant-a", "builder-a"))[0].skill_count == 1
    assert await restarted.list_for_user("tenant-a", "builder-a") == [updated]
    version = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="builder-a",
        name="report-agent",
        version="1.0.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash="a" * 64,
        created_at=datetime.now(UTC),
        snapshot={
            "manifest": {"metadata": {"name": "report-agent"}},
            "skill_snapshots": [
                {
                    "name": "report",
                    "description": "Report",
                    "files": [{"path": "SKILL.md", "content_base64": "AA=="}],
                }
            ],
        },
    )
    registry = PostgresAgentRegistry(sessions, SkillBlobStore(store))
    await registry.add(version)
    async with sessions() as db:
        row = (await db.scalars(select(AgentVersionRow))).one()
        assert "files" not in row.payload["snapshot"]["skill_snapshots"][0]
    assert await registry.get("tenant-a", "builder-a", version.name, version.version) == version
    assert await registry.list_for_user("tenant-a", "builder-a") == [version]
    catalog = await registry.list_catalog_for_user("tenant-a", "builder-a")
    assert catalog[0].snapshot["skill_snapshots"] == [{"name": "report", "description": "Report"}]
    # References can be converted back exactly for a rollback to older readers.
    async with sessions() as db:
        row = (await db.scalars(select(AgentDraftRow))).one()
        row.payload = await SkillBlobStore(store).transform(row.tenant_id, row.payload, inline=True)
        await db.commit()
    assert await legacy.get("tenant-a", "builder-a", original.draft_id) == updated
