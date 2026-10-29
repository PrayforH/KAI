from datetime import UTC, datetime

import pytest

from harness.projects.models import ProjectDocument
from harness.projects.pg_repositories import PostgresProjectResourceRepository
from harness.storage.models import InputArtifactRow
from tests.integration.storage.conftest import DatabaseFixture


@pytest.mark.asyncio
async def test_project_document_hash_uses_owned_input_and_preserves_missing_metadata(
    database: DatabaseFixture,
):
    _, sessions = database
    repository = PostgresProjectResourceRepository(sessions)
    now = datetime.now(UTC)
    for artifact_id in ("owned", "foreign-owner", "legacy"):
        await repository.add_document(
            ProjectDocument(
                tenantId="tenant-a",
                userId="owner",
                projectId="project-a",
                inputArtifactId=artifact_id,
                name=f"{artifact_id}.txt",
                mediaType="text/plain",
                sizeBytes=7,
                createdAt=now,
            )
        )
    async with sessions() as session:
        session.add_all(
            [
                InputArtifactRow(
                    tenant_id="tenant-a",
                    user_id="owner",
                    input_artifact_id="owned",
                    payload={"sha256": "a" * 64},
                ),
                InputArtifactRow(
                    tenant_id="tenant-b",
                    user_id="owner",
                    input_artifact_id="owned",
                    payload={"sha256": "b" * 64},
                ),
                InputArtifactRow(
                    tenant_id="tenant-a",
                    user_id="someone-else",
                    input_artifact_id="foreign-owner",
                    payload={"sha256": "c" * 64},
                ),
            ]
        )
        await session.commit()
    documents = {
        item.input_artifact_id: item
        for item in await repository.list_documents("tenant-a", "project-a")
    }
    assert len(documents) == 3
    assert documents["owned"].sha256 == "a" * 64
    assert documents["foreign-owner"].sha256 is None
    assert documents["legacy"].sha256 is None
    assert await repository.list_documents("tenant-b", "project-a") == []
