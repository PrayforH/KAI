"""Project persistence port and in-memory adapter."""

from __future__ import annotations

import asyncio
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import Project


class ProjectRepository(Protocol):
    async def add(self, project: Project) -> None: ...
    async def get(self, tenant_id: str, project_id: str) -> Project: ...
    async def list_for_user(
        self, tenant_id: str, user_id: str, *, include_archived: bool
    ) -> list[Project]: ...
    async def replace(self, project: Project) -> None: ...
    async def delete(self, tenant_id: str, project_id: str) -> None: ...


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self._items: dict[str, Project] = {}
        self._lock = asyncio.Lock()

    async def add(self, project: Project) -> None:
        async with self._lock:
            if project.project_id in self._items:
                raise ConflictError(f"Project already exists: {project.project_id}")
            if any(
                item.tenant_id == project.tenant_id
                and item.user_id == project.user_id
                and item.name == project.name
                for item in self._items.values()
            ):
                raise ConflictError(f"Project name already used: {project.name}")
            self._items[project.project_id] = project

    async def get(self, tenant_id: str, project_id: str) -> Project:
        try:
            project = self._items[project_id]
        except KeyError as error:
            raise NotFoundError(f"Project not found: {project_id}") from error
        if project.tenant_id != tenant_id:
            raise NotFoundError(f"Project not found: {project_id}")
        return project

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, include_archived: bool
    ) -> list[Project]:
        items = [
            item
            for item in self._items.values()
            if item.tenant_id == tenant_id
            and item.user_id == user_id
            and (include_archived or item.archived_at is None)
        ]
        return sorted(items, key=lambda item: (item.created_at, item.project_id))

    async def replace(self, project: Project) -> None:
        async with self._lock:
            current = self._items.get(project.project_id)
            if current is None or current.tenant_id != project.tenant_id:
                raise NotFoundError(f"Project not found: {project.project_id}")
            if any(
                item.project_id != project.project_id
                and item.tenant_id == project.tenant_id
                and item.user_id == project.user_id
                and item.name == project.name
                for item in self._items.values()
            ):
                raise ConflictError(f"Project name already used: {project.name}")
            self._items[project.project_id] = project

    async def delete(self, tenant_id: str, project_id: str) -> None:
        async with self._lock:
            project = self._items.get(project_id)
            if project is None or project.tenant_id != tenant_id:
                raise NotFoundError(f"Project not found: {project_id}")
            del self._items[project_id]
