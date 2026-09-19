"""Tests for user-owned projects and their task bindings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.repositories import InMemoryProjectRepository
from harness.projects.service import ProjectService


def build_service(clock=None) -> tuple[ProjectService, list[tuple[str, str, str, str | None]]]:
    moves: list[tuple[str, str, str, str | None]] = []
    counter = {"n": 0}

    async def writer(
        tenant_id: str, user_id: str, thread_id: str, project_id: str | None
    ) -> str | None:
        moves.append((tenant_id, user_id, thread_id, project_id))
        return project_id

    def seq(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    service = ProjectService(
        InMemoryProjectRepository(),
        clock=clock or (lambda: datetime(2026, 9, 20, 8, 0, tzinfo=UTC)),
        id_generator=seq,
    )
    service.configure_task_project_writer(writer)
    return service, moves


@pytest.mark.asyncio
async def test_create_lists_and_trims_the_name() -> None:
    service, _moves = build_service()
    project = await service.create(tenant_id="t", user_id="u", name="  金融办  ")
    assert project.name == "金融办"
    listed = await service.list(tenant_id="t", user_id="u")
    assert [item.project_id for item in listed] == [project.project_id]


@pytest.mark.asyncio
async def test_duplicate_names_are_rejected_per_user() -> None:
    service, _moves = build_service()
    await service.create(tenant_id="t", user_id="u", name="金融办")
    with pytest.raises(ConflictError):
        await service.create(tenant_id="t", user_id="u", name="金融办")
    # Another user may reuse the same name.
    other = await service.create(tenant_id="t", user_id="u2", name="金融办")
    assert other.name == "金融办"


@pytest.mark.asyncio
async def test_empty_names_are_rejected() -> None:
    service, _moves = build_service()
    with pytest.raises(ConflictError):
        await service.create(tenant_id="t", user_id="u", name="   ")


@pytest.mark.asyncio
async def test_rename_and_archive_hide_from_the_default_list() -> None:
    service, _moves = build_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    renamed = await service.update(
        tenant_id="t", user_id="u", project_id=project.project_id, name="金融办 2026"
    )
    assert renamed.name == "金融办 2026"
    archived = await service.update(
        tenant_id="t", user_id="u", project_id=project.project_id, archived=True
    )
    assert archived.archived_at is not None
    assert await service.list(tenant_id="t", user_id="u") == []
    assert len(await service.list(tenant_id="t", user_id="u", include_archived=True)) == 1


@pytest.mark.asyncio
async def test_other_users_cannot_touch_a_project() -> None:
    service, _moves = build_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    with pytest.raises(NotFoundError):
        await service.get(tenant_id="t", user_id="intruder", project_id=project.project_id)
    with pytest.raises(NotFoundError):
        await service.delete(tenant_id="t", user_id="intruder", project_id=project.project_id)


@pytest.mark.asyncio
async def test_assign_task_validates_ownership_then_moves_it() -> None:
    service, moves = build_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    assigned = await service.assign_task(
        tenant_id="t", user_id="u", thread_id="thread-1", project_id=project.project_id
    )
    assert assigned == project.project_id
    assert moves == [("t", "u", "thread-1", project.project_id)]
    with pytest.raises(NotFoundError):
        await service.assign_task(
            tenant_id="t", user_id="intruder", thread_id="thread-1", project_id=project.project_id
        )


@pytest.mark.asyncio
async def test_assign_task_with_null_returns_the_task_to_任务() -> None:
    service, moves = build_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    await service.assign_task(
        tenant_id="t", user_id="u", thread_id="thread-1", project_id=project.project_id
    )
    cleared = await service.assign_task(
        tenant_id="t", user_id="u", thread_id="thread-1", project_id=None
    )
    assert cleared is None
    assert moves[-1] == ("t", "u", "thread-1", None)


@pytest.mark.asyncio
async def test_delete_removes_the_project() -> None:
    service, _moves = build_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    await service.delete(tenant_id="t", user_id="u", project_id=project.project_id)
    with pytest.raises(NotFoundError):
        await service.get(tenant_id="t", user_id="u", project_id=project.project_id)


_ = Any
