from datetime import UTC, datetime

import pytest

from harness.core.models import AguiThreadBinding
from harness.storage.models import AguiThreadBindingRow
from harness.storage.platform_repositories import PostgresAguiThreadBindingRepository


@pytest.mark.asyncio
async def test_project_detach_preserves_task_payload_and_other_tenants(database):
    _, sessions = database
    repo = PostgresAguiThreadBindingRepository(sessions)
    now = datetime.now(UTC)
    for tenant, project in [("qa", "removed"), ("qa", "kept"), ("other", "removed")]:
        await repo.add(AguiThreadBinding(
            tenant_id=tenant, user_id="owner", thread_id=project, session_id=f"s-{project}",
            project_id=project, title="Keep this title", created_at=now, updated_at=now,
        ))
    assert await repo.clear_project("qa", "removed") == 1
    detached = await repo.get_by_thread("qa", "owner", "removed")
    assert detached.project_id is None and detached.title == "Keep this title"
    assert detached.session_id == "s-removed" and detached.updated_at == now
    assert (await repo.get_by_thread("qa", "owner", "kept")).project_id == "kept"
    assert (await repo.get_by_thread("other", "owner", "removed")).project_id == "removed"
    async with sessions() as session:
        row = await session.get(AguiThreadBindingRow, ("qa", "owner", "removed"))
        assert row is not None and row.project_id is None
        assert row.payload["project_id"] is None
    assert await repo.clear_project("qa", "removed") == 0
