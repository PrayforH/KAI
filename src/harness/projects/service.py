"""Use cases for user-owned projects that group tasks."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import Project


def _default_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


class ProjectService:
    def __init__(
        self,
        repository,
        *,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
        task_project_writer=None,
        task_project_clearer=None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = id_generator or _default_id
        # Moves a task into (or out of) a project; supplied by the composition
        # root because the binding lives with the AG-UI thread record.
        self._task_project_writer = task_project_writer
        self._task_project_clearer = task_project_clearer

    def configure_task_project_clearer(self, clearer) -> None:
        self._task_project_clearer = clearer

    def configure_task_project_writer(self, writer) -> None:
        if self._task_project_writer is not None:
            raise RuntimeError("task project writer is already configured")
        self._task_project_writer = writer

    async def create(
        self, *, tenant_id: str, user_id: str, name: str
    ) -> Project:
        trimmed = name.strip()
        if not trimmed:
            raise ConflictError("Project name cannot be empty")
        now = self._clock()
        project = Project(
            tenantId=tenant_id,
            projectId=self._ids("project"),
            userId=user_id,
            name=trimmed,
            createdAt=now,
            updatedAt=now,
        )
        await self._repository.add(project)
        return project

    async def list(
        self, *, tenant_id: str, user_id: str, include_archived: bool = False
    ) -> list[Project]:
        return await self._repository.list_for_user(
            tenant_id, user_id, include_archived=include_archived
        )

    async def get(self, *, tenant_id: str, user_id: str, project_id: str) -> Project:
        project = await self._repository.get(tenant_id, project_id)
        if project.user_id != user_id:
            raise NotFoundError(f"Project not found: {project_id}")
        return project

    async def update(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        name: str | None = None,
        archived: bool | None = None,
    ) -> Project:
        current = await self.get(
            tenant_id=tenant_id, user_id=user_id, project_id=project_id
        )
        updates: dict[str, object] = {"updated_at": self._clock()}
        if name is not None:
            trimmed = name.strip()
            if not trimmed:
                raise ConflictError("Project name cannot be empty")
            updates["name"] = trimmed
        if archived is not None:
            updates["archived_at"] = self._clock() if archived else None
        updated = current.model_copy(update=updates)
        await self._repository.replace(updated)
        return updated

    async def delete(self, *, tenant_id: str, user_id: str, project_id: str) -> None:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        # Detach the tasks first: a dangling project_id would hide them from both
        # the 项目 and 任务 sections.
        if self._task_project_clearer is not None:
            await self._task_project_clearer(tenant_id, project_id)
        await self._repository.delete(tenant_id, project_id)

    async def assign_task(
        self,
        *,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        project_id: str | None,
    ) -> str | None:
        """Move one task into a project, or out of every project when null."""

        if project_id is not None:
            # Validates ownership and existence before the task is touched.
            await self.get(
                tenant_id=tenant_id, user_id=user_id, project_id=project_id
            )
        if self._task_project_writer is None:
            raise ConflictError("Task project binding is unavailable")
        return await self._task_project_writer(
            tenant_id, user_id, thread_id, project_id
        )
