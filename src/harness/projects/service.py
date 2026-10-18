"""Use cases for user-owned projects that group tasks."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import (
    MAX_PROJECT_DOCUMENTS,
    MAX_PROJECT_KNOWLEDGE_BASES,
    MAX_PROJECT_LINK_TITLE,
    MAX_PROJECT_LINKS,
    MAX_PROJECT_MEMORIES,
    MAX_PROJECT_SKILLS,
    Project,
    ProjectDocument,
    ProjectLink,
    ProjectMemory,
    ProjectResources,
    ProjectRunContext,
)
from harness.studio.platform_skills import platform_skill_package


def _default_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _normalize_skill_references(references: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(references)) != len(references):
        raise ConflictError("duplicate project Skill reference")
    if len(references) > MAX_PROJECT_SKILLS:
        raise ConflictError(f"a project accepts at most {MAX_PROJECT_SKILLS} Skill references")
    normalized: list[str] = []
    for reference in references:
        package_id, separator, revision = reference.partition("@")
        if not separator or not revision.isdigit():
            raise ConflictError(f"project Skill reference must be packageId@revision: {reference}")
        try:
            package = platform_skill_package(package_id, int(revision))
        except (ConflictError, NotFoundError) as error:
            raise ConflictError(str(error)) from error
        normalized.append(f"{package.package_id}@{package.revision}")
    return tuple(normalized)


def _normalize_link_url(url: str) -> str:
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConflictError("project link must be an http(s) URL")
    return value


def project_knowledge_resolver(resolve_bindings):
    """Adapt the knowledge ACL resolver to the project binding signature."""

    async def validate(tenant_id: str, user_id: str, references: tuple[str, ...]) -> None:
        await resolve_bindings(tenant_id, user_id, references, ())

    return validate


class ProjectService:
    def __init__(
        self,
        repository,
        *,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
        task_project_writer=None,
        task_project_clearer=None,
        resources=None,
        document_resolver=None,
        knowledge_resolver=None,
    ) -> None:
        self._repository = repository
        self._resources = resources
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = id_generator or _default_id
        # Moves a task into (or out of) a project; supplied by the composition
        # root because the binding lives with the AG-UI thread record.
        self._task_project_writer = task_project_writer
        self._task_project_clearer = task_project_clearer
        self._document_resolver = document_resolver
        self._knowledge_resolver = knowledge_resolver

    def configure_document_resolver(self, resolver) -> None:
        self._document_resolver = resolver

    def configure_knowledge_resolver(self, resolver) -> None:
        self._knowledge_resolver = resolver

    async def _normalize_knowledge_references(
        self,
        *,
        tenant_id: str,
        user_id: str,
        references: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(set(references)) != len(references):
            raise ConflictError("duplicate project knowledge base reference")
        if len(references) > MAX_PROJECT_KNOWLEDGE_BASES:
            raise ConflictError(
                f"a project accepts at most {MAX_PROJECT_KNOWLEDGE_BASES} knowledge bases"
            )
        if not references:
            return ()
        if self._knowledge_resolver is None:
            raise ConflictError("知识检索服务未配置")
        await self._knowledge_resolver(tenant_id, user_id, references)
        return references

    def configure_task_project_clearer(self, clearer) -> None:
        self._task_project_clearer = clearer

    def configure_task_project_writer(self, writer) -> None:
        if self._task_project_writer is not None:
            raise RuntimeError("task project writer is already configured")
        self._task_project_writer = writer

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        instructions: str = "",
        skill_references: tuple[str, ...] = (),
        knowledge_references: tuple[str, ...] = (),
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
            instructions=instructions.strip(),
            skillReferences=_normalize_skill_references(skill_references),
            knowledgeReferences=await self._normalize_knowledge_references(
                tenant_id=tenant_id,
                user_id=user_id,
                references=knowledge_references,
            ),
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
        instructions: str | None = None,
        skill_references: tuple[str, ...] | None = None,
        knowledge_references: tuple[str, ...] | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> Project:
        current = await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        updates: dict[str, object] = {"updated_at": self._clock()}
        if name is not None:
            trimmed = name.strip()
            if not trimmed:
                raise ConflictError("Project name cannot be empty")
            updates["name"] = trimmed
        if instructions is not None:
            updates["instructions"] = instructions.strip()
        if skill_references is not None:
            updates["skill_references"] = _normalize_skill_references(skill_references)
        if knowledge_references is not None:
            updates["knowledge_references"] = await self._normalize_knowledge_references(
                tenant_id=tenant_id,
                user_id=user_id,
                references=knowledge_references,
            )
        if pinned is not None:
            updates["pinned_at"] = self._clock() if pinned else None
        if archived is not None:
            updates["archived_at"] = self._clock() if archived else None
        updated = current.model_copy(update=updates)
        await self._repository.replace(updated)
        return updated

    async def instructions_for(self, *, tenant_id: str, user_id: str, project_id: str) -> str:
        """Return the project's instructions after checking ownership.

        A Run resolves this once at creation time; callers treat the returned
        text as an immutable snapshot rather than re-reading it later.
        """

        project = await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        return project.instructions

    async def delete(self, *, tenant_id: str, user_id: str, project_id: str) -> None:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        # Detach the tasks first: a dangling project_id would hide them from both
        # the 项目 and 任务 sections.
        if self._task_project_clearer is not None:
            await self._task_project_clearer(tenant_id, project_id)
        if self._resources is not None:
            await self._resources.delete_for_project(tenant_id, project_id)
        await self._repository.delete(tenant_id, project_id)

    async def resources(self, *, tenant_id: str, user_id: str, project_id: str) -> ProjectResources:
        project = await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            return ProjectResources(project=project)
        return ProjectResources(
            project=project,
            memories=tuple(await self._resources.list_memories(tenant_id, project_id)),
            documents=tuple(await self._resources.list_documents(tenant_id, project_id)),
            links=tuple(await self._resources.list_links(tenant_id, project_id)),
        )

    async def add_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        content: str,
    ) -> ProjectMemory:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        trimmed = content.strip()
        if not trimmed:
            raise ConflictError("Project memory cannot be empty")
        existing = await self._resources.list_memories(tenant_id, project_id)
        if len(existing) >= MAX_PROJECT_MEMORIES:
            raise ConflictError(f"a project accepts at most {MAX_PROJECT_MEMORIES} memories")
        now = self._clock()
        memory = ProjectMemory(
            tenantId=tenant_id,
            userId=user_id,
            projectId=project_id,
            memoryId=self._ids("project_memory"),
            content=trimmed,
            createdAt=now,
            updatedAt=now,
        )
        await self._resources.add_memory(memory)
        return memory

    async def update_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        memory_id: str,
        content: str,
    ) -> ProjectMemory:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        trimmed = content.strip()
        if not trimmed:
            raise ConflictError("Project memory cannot be empty")
        current = await self._resources.get_memory(tenant_id, project_id, memory_id)
        updated = current.model_copy(update={"content": trimmed, "updated_at": self._clock()})
        await self._resources.replace_memory(updated)
        return updated

    async def delete_memory(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        memory_id: str,
    ) -> None:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        await self._resources.delete_memory(tenant_id, project_id, memory_id)

    async def attach_document(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        input_artifact_id: str,
    ) -> ProjectDocument:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        if self._document_resolver is None:
            raise ConflictError("Project document verification is unavailable")
        existing = await self._resources.list_documents(tenant_id, project_id)
        if len(existing) >= MAX_PROJECT_DOCUMENTS:
            raise ConflictError(f"a project accepts at most {MAX_PROJECT_DOCUMENTS} documents")
        artifact = await self._document_resolver(tenant_id, user_id, input_artifact_id)
        document = ProjectDocument(
            tenantId=tenant_id,
            userId=user_id,
            projectId=project_id,
            inputArtifactId=input_artifact_id,
            name=artifact.name,
            mediaType=artifact.media_type,
            sizeBytes=artifact.size_bytes or 0,
            sha256=getattr(artifact, "sha256", None),
            createdAt=self._clock(),
        )
        await self._resources.add_document(document)
        return document

    async def detach_document(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        input_artifact_id: str,
    ) -> None:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        await self._resources.delete_document(tenant_id, project_id, input_artifact_id)

    async def add_link(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        url: str,
        title: str | None = None,
    ) -> ProjectLink:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        existing = await self._resources.list_links(tenant_id, project_id)
        if len(existing) >= MAX_PROJECT_LINKS:
            raise ConflictError(f"a project accepts at most {MAX_PROJECT_LINKS} links")
        normalized = _normalize_link_url(url)
        if any(item.url == normalized for item in existing):
            raise ConflictError("project link already exists")
        link = ProjectLink(
            tenantId=tenant_id,
            userId=user_id,
            projectId=project_id,
            linkId=self._ids("project_link"),
            url=normalized,
            title=(title or "").strip()[:MAX_PROJECT_LINK_TITLE],
            createdAt=self._clock(),
        )
        await self._resources.add_link(link)
        return link

    async def delete_link(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: str,
        link_id: str,
    ) -> None:
        await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._resources is None:
            raise ConflictError("Project resource store is unavailable")
        await self._resources.delete_link(tenant_id, project_id, link_id)

    async def context_for_run(
        self, tenant_id: str, user_id: str, project_id: str
    ) -> ProjectRunContext | None:
        """Capture the project's context for one Run.

        A task can outlive its project (the project is a container, not a
        dependency), so a missing project simply yields no context.
        """

        try:
            project = await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        except NotFoundError:
            return None
        memories: tuple[ProjectMemory, ...] = ()
        documents: tuple[ProjectDocument, ...] = ()
        links: tuple[ProjectLink, ...] = ()
        if self._resources is not None:
            memories = tuple(await self._resources.list_memories(tenant_id, project_id))
            documents = tuple(await self._resources.list_documents(tenant_id, project_id))
            links = tuple(await self._resources.list_links(tenant_id, project_id))
        sections: list[str] = []
        if project.instructions.strip():
            sections.append(f"## 项目说明\n{project.instructions.strip()}")
        if memories:
            sections.append("## 项目记忆\n" + "\n".join(f"- {item.content}" for item in memories))
        if links:
            sections.append(
                "## 项目网页\n"
                + "\n".join(f"- {item.title or item.url}：{item.url}" for item in links)
            )
        if project.skill_references:
            labels: list[str] = []
            for reference in project.skill_references:
                package_id, _, revision = reference.partition("@")
                try:
                    package = platform_skill_package(package_id, int(revision))
                    labels.append(f"- {package.display_name}（{reference}）")
                except (ConflictError, NotFoundError, ValueError):
                    labels.append(f"- {reference}")
            sections.append("## 项目技能\n" + "\n".join(labels))
        text = (
            f'<project_context project="{project.name}">\n'
            + "\n\n".join(sections)
            + "\n</project_context>"
            if sections
            else ""
        )
        return ProjectRunContext(
            projectId=project_id,
            resourceManifest={
                "projectId": project_id,
                "name": project.name,
                "updatedAt": project.updated_at.isoformat(),
                "instructionsHash": hashlib.sha256(project.instructions.encode()).hexdigest(),
                "memories": [
                    {
                        "id": item.memory_id,
                        "updatedAt": item.updated_at.isoformat(),
                        "contentHash": hashlib.sha256(item.content.encode()).hexdigest(),
                    }
                    for item in memories
                ],
                "documents": [
                    {"id": item.input_artifact_id, "name": item.name, "sha256": item.sha256}
                    for item in documents
                ],
                "links": [
                    {"id": item.link_id, "url": item.url, "title": item.title} for item in links
                ],
            },
            text=text,
            documentIds=tuple(item.input_artifact_id for item in documents),
            skillReferences=project.skill_references,
            knowledgeReferences=project.knowledge_references,
            memoryCount=len(memories),
            documentCount=len(documents),
            skillCount=len(project.skill_references),
            knowledgeCount=len(project.knowledge_references),
            linkCount=len(links),
        )

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
            await self.get(tenant_id=tenant_id, user_id=user_id, project_id=project_id)
        if self._task_project_writer is None:
            raise ConflictError("Task project binding is unavailable")
        return await self._task_project_writer(tenant_id, user_id, thread_id, project_id)
