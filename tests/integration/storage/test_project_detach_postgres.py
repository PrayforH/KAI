from datetime import UTC, datetime

import pytest

from harness.core.models import AguiThreadBinding
from harness.storage.platform_repositories import PostgresAguiThreadBindingRepository


@pytest.mark.asyncio
async def test_deleting_project_clears_json_and_index_without_changing_other_tasks(database):
    _, sessions = database
    repository = PostgresAguiThreadBindingRepository(sessions)
    now = datetime.now(UTC)
    for thread, project, tenant in [
        ("one", "project-a", "tenant-a"),
        ("two", "project-a", "tenant-a"),
        ("other-project", "project-b", "tenant-a"),
        ("other-tenant", "project-a", "tenant-b"),
    ]:
        await repository.add(
            AguiThreadBinding(
                tenant_id=tenant,
                user_id="user-1",
                thread_id=thread,
                session_id=f"session-{thread}",
                project_id=project,
                title=f"Title {thread}",
                created_at=now,
                updated_at=now,
            )
        )
    assert await repository.clear_project("tenant-a", "project-a") == 2
    for thread in ["one", "two"]:
        row = await repository.get_by_thread("tenant-a", "user-1", thread)
        assert row.project_id is None
        assert row.title == f"Title {thread}"
    assert (
        await repository.get_by_thread("tenant-a", "user-1", "other-project")
    ).project_id == "project-b"
    assert (
        await repository.get_by_thread("tenant-b", "user-1", "other-tenant")
    ).project_id == "project-a"
    assert await repository.clear_project("tenant-a", "project-a") == 0
