"""Project persistence port and in-memory adapter."""

from __future__ import annotations

import asyncio
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import Project, ProjectDocument, ProjectMemory


class ProjectRepository(Protocol):
    async def add(self, project: Project) -> None: ...
    async def get(self, tenant_id: str, project_id: str) -> Project: ...
    async def list_for_user(
        self, tenant_id: str, user_id: str, *, include_archived: bool
    ) -> list[Project]: ...
    async def replace(self, project: Project) -> None: ...
    async def delete(self, tenant_id: str, project_id: str) -> None: ...


class ProjectResourceRepository(Protocol):
    async def list_memories(
        self, tenant_id: str, project_id: str
    ) -> list[ProjectMemory]: ...
    async def add_memory(self, memory: ProjectMemory) -> None: ...
    async def get_memory(
        self, tenant_id: str, project_id: str, memory_id: str
    ) -> ProjectMemory: ...
    async def replace_memory(self, memory: ProjectMemory) -> None: ...
    async def delete_memory(
        self, tenant_id: str, project_id: str, memory_id: str
    ) -> None: ...
    async def list_documents(
        self, tenant_id: str, project_id: str
    ) -> list[ProjectDocument]: ...
    async def add_document(self, document: ProjectDocument) -> None: ...
    async def delete_document(
        self, tenant_id: str, project_id: str, input_artifact_id: str
    ) -> None: ...
    async def delete_for_project(self, tenant_id: str, project_id: str) -> None: ...


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


class InMemoryProjectResourceRepository:
    def __init__(self) -> None:
        self._memories: dict[tuple[str, str], ProjectMemory] = {}
        self._documents: dict[tuple[str, str, str], ProjectDocument] = {}
        self._lock = asyncio.Lock()

    async def list_memories(
        self, tenant_id: str, project_id: str
    ) -> list[ProjectMemory]:
        return sorted(
            (
                item
                for item in self._memories.values()
                if item.tenant_id == tenant_id and item.project_id == project_id
            ),
            key=lambda item: (item.created_at, item.memory_id),
        )

    async def add_memory(self, memory: ProjectMemory) -> None:
        async with self._lock:
            key = (memory.tenant_id, memory.memory_id)
            if key in self._memories:
                raise ConflictError(f"project memory already exists: {memory.memory_id}")
            self._memories[key] = memory

    async def get_memory(
        self, tenant_id: str, project_id: str, memory_id: str
    ) -> ProjectMemory:
        memory = self._memories.get((tenant_id, memory_id))
        if memory is None or memory.project_id != project_id:
            raise NotFoundError(f"project memory not found: {memory_id}")
        return memory

    async def replace_memory(self, memory: ProjectMemory) -> None:
        async with self._lock:
            key = (memory.tenant_id, memory.memory_id)
            current = self._memories.get(key)
            if current is None or current.project_id != memory.project_id:
                raise NotFoundError(f"project memory not found: {memory.memory_id}")
            self._memories[key] = memory

    async def delete_memory(
        self, tenant_id: str, project_id: str, memory_id: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, memory_id)
            current = self._memories.get(key)
            if current is None or current.project_id != project_id:
                raise NotFoundError(f"project memory not found: {memory_id}")
            del self._memories[key]

    async def list_documents(
        self, tenant_id: str, project_id: str
    ) -> list[ProjectDocument]:
        return sorted(
            (
                item
                for item in self._documents.values()
                if item.tenant_id == tenant_id and item.project_id == project_id
            ),
            key=lambda item: (item.created_at, item.input_artifact_id),
        )

    async def add_document(self, document: ProjectDocument) -> None:
        async with self._lock:
            key = (
                document.tenant_id,
                document.project_id,
                document.input_artifact_id,
            )
            if key in self._documents:
                raise ConflictError(
                    f"document already attached: {document.input_artifact_id}"
                )
            self._documents[key] = document

    async def delete_document(
        self, tenant_id: str, project_id: str, input_artifact_id: str
    ) -> None:
        async with self._lock:
            key = (tenant_id, project_id, input_artifact_id)
            if key not in self._documents:
                raise NotFoundError(f"project document not found: {input_artifact_id}")
            del self._documents[key]

    async def delete_for_project(self, tenant_id: str, project_id: str) -> None:
        async with self._lock:
            for key in [
                key
                for key, item in self._memories.items()
                if item.tenant_id == tenant_id and item.project_id == project_id
            ]:
                del self._memories[key]
            for key in [
                key
                for key, item in self._documents.items()
                if item.tenant_id == tenant_id and item.project_id == project_id
            ]:
                del self._documents[key]
