import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.core.errors import NotFoundError
from harness.core.models import AguiThreadBinding
from harness.storage.platform_repositories import PostgresAguiThreadBindingRepository


@pytest.mark.asyncio
async def test_read_state_survives_concurrent_title_archive_and_delayed_reads(database):
    _, sessions = database
    repo = PostgresAguiThreadBindingRepository(sessions)
    now = datetime.now(UTC)
    await repo.add(
        AguiThreadBinding(
            tenant_id="qa",
            user_id="qa",
            thread_id="read-test",
            session_id="s",
            created_at=now,
            updated_at=now,
        )
    )
    await asyncio.gather(
        *[
            repo.mark_read("qa", "qa", "read-test", read_at=now + timedelta(seconds=i))
            for i in [5, 1, 8, 2, 7, 4, 9, 3, 0, 6]
        ],
        repo.update_title(
            "qa", "qa", "read-test", title="Renamed", source="model", generated_at=now
        ),
        repo.set_archived("qa", "qa", "read-test", archived_at=now),
    )
    value = await PostgresAguiThreadBindingRepository(sessions).get_by_thread(
        "qa", "qa", "read-test"
    )
    assert value.last_read_at == now + timedelta(seconds=9)
    assert value.title == "Renamed" and value.archived_at == now and value.updated_at == now
    try:
        await repo.mark_read("qa", "other", "read-test", read_at=now)
        raise AssertionError("cross-user write permitted")
    except NotFoundError:
        pass
