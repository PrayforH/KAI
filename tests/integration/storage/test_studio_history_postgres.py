from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import InMemorySessionRepository
from harness.core.models import Session
from harness.storage.platform_repositories import PostgresSessionRepository


@pytest.mark.asyncio
async def test_preview_history_scope_escaping_order_and_limit(database):
    _, sessions = database
    now = datetime.now(UTC)
    for repository in (PostgresSessionRepository(sessions), InMemorySessionRepository()):
        for index, (tenant, user, draft, environment) in enumerate([
            ("tenant-a", "owner", "draft_id", "preview"),
            ("tenant-a", "owner", "draft_id", "preview"),
            ("tenant-b", "owner", "draft_id", "preview"),
            ("tenant-a", "other", "draft_id", "preview"),
            ("tenant-a", "owner", "draftXid", "preview"),
            ("tenant-a", "owner", "draft_id_other", "preview"),
            ("tenant-a", "owner", "draft_id", "production"),
        ]):
            await repository.add(Session(
                session_id=f"history-{index}", tenant_id=tenant, user_id=user,
                agent_name="same-agent", agent_version=f"preview-{draft}-1-hash",
                environment=environment, created_at=now + timedelta(seconds=index),
            ))
        rows = await repository.list_studio_previews("tenant-a", "owner", "draft_id", limit=10)
        assert [row.session_id for row in rows] == ["history-1", "history-0"]
        rows = await repository.list_studio_previews("tenant-a", "owner", "draft_id", limit=1)
        assert [row.session_id for row in rows] == ["history-1"]
