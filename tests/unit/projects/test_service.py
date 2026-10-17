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
async def test_assign_task_with_null_returns_the_task_to_tasks() -> None:
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


@pytest.mark.asyncio
async def test_delete_detaches_the_projects_tasks_first() -> None:
    cleared: list[tuple[str, str]] = []

    async def clearer(tenant_id: str, project_id: str) -> int:
        cleared.append((tenant_id, project_id))
        return 3

    service, _moves = build_service()
    service.configure_task_project_clearer(clearer)
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    await service.delete(tenant_id="t", user_id="u", project_id=project.project_id)
    assert cleared == [("t", project.project_id)]


def build_resource_service():
    from types import SimpleNamespace

    from harness.projects.repositories import InMemoryProjectResourceRepository

    resources = InMemoryProjectResourceRepository()
    service = ProjectService(
        InMemoryProjectRepository(),
        resources=resources,
        clock=lambda: datetime(2026, 10, 17, 9, 0, tzinfo=UTC),
        id_generator=lambda prefix: f"{prefix}-1",
    )
    seen: list[tuple[str, str, str]] = []

    async def resolver(tenant_id: str, user_id: str, artifact_id: str):
        seen.append((tenant_id, user_id, artifact_id))
        return SimpleNamespace(
            name="brief.md", media_type="text/markdown", size_bytes=12
        )

    service.configure_document_resolver(resolver)
    return service, resources, seen


@pytest.mark.asyncio
async def test_project_instructions_round_trip_and_reach_run_context() -> None:
    service, _resources, _seen = build_resource_service()
    project = await service.create(
        tenant_id="t",
        user_id="u",
        name="金融办",
        instructions="  只输出结论  ",
    )
    assert project.instructions == "只输出结论"
    updated = await service.update(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        instructions="先给证据，再给结论",
    )
    assert updated.instructions == "先给证据，再给结论"
    context = await service.context_for_run(
        tenant_id="t", user_id="u", project_id=project.project_id
    )
    assert context is not None
    assert "先给证据，再给结论" in context.text
    assert context.document_ids == ()


@pytest.mark.asyncio
async def test_project_memories_are_listed_and_injected() -> None:
    service, _resources, _seen = build_resource_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    memory = await service.add_memory(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        content=" 口径以 2026 版为准 ",
    )
    assert memory.content == "口径以 2026 版为准"
    updated = await service.update_memory(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        memory_id=memory.memory_id,
        content="口径以 2026 版为准，旧版作废",
    )
    assert updated.content.endswith("旧版作废")
    context = await service.context_for_run(
        tenant_id="t", user_id="u", project_id=project.project_id
    )
    assert context is not None
    assert "## 项目记忆" in context.text
    assert "旧版作废" in context.text
    assert context.memory_count == 1
    await service.delete_memory(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        memory_id=memory.memory_id,
    )
    context = await service.context_for_run(
        tenant_id="t", user_id="u", project_id=project.project_id
    )
    assert context is not None
    assert context.memory_count == 0


@pytest.mark.asyncio
async def test_project_documents_are_verified_and_injected_into_every_run() -> None:
    service, _resources, seen = build_resource_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    document = await service.attach_document(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        input_artifact_id="input_artifact-1",
    )
    assert document.name == "brief.md"
    assert seen == [("t", "u", "input_artifact-1")]
    context = await service.context_for_run(
        tenant_id="t", user_id="u", project_id=project.project_id
    )
    assert context is not None
    assert context.document_ids == ("input_artifact-1",)
    assert context.document_count == 1
    await service.detach_document(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        input_artifact_id="input_artifact-1",
    )
    context = await service.context_for_run(
        tenant_id="t", user_id="u", project_id=project.project_id
    )
    assert context is not None
    assert context.document_ids == ()


@pytest.mark.asyncio
async def test_project_skills_are_pinned_and_reach_run_context() -> None:
    service, _resources, _seen = build_resource_service()
    project = await service.create(
        tenant_id="t",
        user_id="u",
        name="金融办",
        skill_references=("pptx-generator@1",),
    )
    assert project.skill_references == ("pptx-generator@1",)
    context = await service.context_for_run(
        tenant_id="t", user_id="u", project_id=project.project_id
    )
    assert context is not None
    assert context.skill_references == ("pptx-generator@1",)
    assert context.skill_count == 1
    assert "## 项目技能" in context.text
    updated = await service.update(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        skill_references=(),
    )
    assert updated.skill_references == ()


@pytest.mark.asyncio
async def test_unknown_project_skill_reference_is_rejected() -> None:
    service, _resources, _seen = build_resource_service()
    with pytest.raises(ConflictError):
        await service.create(
            tenant_id="t",
            user_id="u",
            name="金融办",
            skill_references=("missing-package@1",),
        )
    with pytest.raises(ConflictError):
        await service.create(
            tenant_id="t",
            user_id="u",
            name="金融办",
            skill_references=("pptx-generator",),
        )


@pytest.mark.asyncio
async def test_deleting_a_project_removes_its_resources() -> None:
    service, resources, _seen = build_resource_service()
    project = await service.create(tenant_id="t", user_id="u", name="金融办")
    await service.add_memory(
        tenant_id="t", user_id="u", project_id=project.project_id, content="记忆"
    )
    await service.attach_document(
        tenant_id="t",
        user_id="u",
        project_id=project.project_id,
        input_artifact_id="input_artifact-1",
    )
    await service.delete(tenant_id="t", user_id="u", project_id=project.project_id)
    assert await resources.list_memories("t", project.project_id) == []
    assert await resources.list_documents("t", project.project_id) == []
    assert (
        await service.context_for_run(
            tenant_id="t", user_id="u", project_id=project.project_id
        )
        is None
    )
