import asyncio
from datetime import UTC, datetime

import pytest

from harness.core.errors import NotFoundError
from harness.projects.models import Project, ProjectResult
from harness.projects.pg_repositories import (
    PostgresProjectRepository,
    PostgresProjectResourceRepository,
)


@pytest.mark.asyncio
async def test_project_result_versions_survive_reopen_and_serialize_concurrent_saves(database):
    _, sessions = database
    projects = PostgresProjectRepository(sessions)
    resources = PostgresProjectResourceRepository(sessions)
    now = datetime.now(UTC)
    await projects.add(
        Project(
            tenantId="t", userId="owner", projectId="p", name="Review", createdAt=now, updatedAt=now
        )
    )

    def result(index):
        return ProjectResult(
            resultId=f"result-{index}",
            tenantId="t",
            userId="owner",
            projectId="p",
            artifactId=f"output-{index}",
            inputArtifactId=f"snapshot-{index}",
            threadId="source-thread",
            threadTitle="Quarterly review",
            runId="run",
            name="report.pdf",
            mediaType="application/pdf",
            sizeBytes=12,
            createdAt=now,
        )

    saved = await asyncio.gather(*(resources.save_result(result(i)) for i in range(4)))
    assert sorted(r.version for r in saved) == [1, 2, 3, 4]
    duplicate = await resources.save_result(result(0).model_copy(update={"result_id": "duplicate"}))
    assert duplicate.result_id == "result-0"
    await resources.finalize_result("t", "p", "result-0", now)
    reopened = PostgresProjectResourceRepository(sessions)
    rows = await reopened.list_results("t", "p")
    assert len(rows) == 4
    assert next(r for r in rows if r.result_id == "result-0").finalized_at == now
    assert await reopened.list_results("another-tenant", "p") == []
    with pytest.raises(NotFoundError):
        await reopened.finalize_result("another-tenant", "p", "result-0", now)
    await reopened.delete_for_project("t", "p")
    assert await reopened.list_results("t", "p") == []
