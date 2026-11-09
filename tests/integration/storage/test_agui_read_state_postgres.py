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


@pytest.mark.asyncio
async def test_task_count_and_pages_exclude_bot_threads_before_pagination(database):
    _, sessions = database
    repo = PostgresAguiThreadBindingRepository(sessions)
    now = datetime.now(UTC)
    task = AguiThreadBinding(
        tenant_id="qa",
        user_id="owner",
        thread_id="older",
        session_id="older-session",
        project_id="project",
        title="Weekly Report",
        created_at=now,
        updated_at=now,
    )
    for index, changes in enumerate(
        [
            {},
            {"thread_id": "report-newer", "title": None},
            {"thread_id": "archived", "archived_at": now},
            {"thread_id": "another-project", "project_id": "other"},
            {"thread_id": "another-user", "user_id": "other"},
            {"thread_id": "bot", "kind": "bot"},
        ]
    ):
        await repo.add(
            task.model_copy(
                update={
                    "session_id": f"session-{index}",
                    "updated_at": now + timedelta(seconds=index),
                    **changes,
                },
            )
        )

    filters = {"kind": "task", "project_id": "project", "query": "REPORT"}
    first = await repo.list_for_user("qa", "owner", limit=1, **filters)
    second = await repo.list_for_user("qa", "owner", limit=1, offset=1, **filters)
    empty = await repo.list_for_user("qa", "owner", limit=1, offset=2, **filters)
    assert [binding.thread_id for binding in first + second] == ["report-newer", "older"]
    assert not empty
    assert await repo.count_for_user("qa", "owner", **filters) == 2
    assert await repo.count_for_user("qa", "owner", archived=True, **filters) == 1
    assert await repo.count_for_user("qa", "owner", **(filters | {"query": "%"})) == 0
