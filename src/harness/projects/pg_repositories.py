"""PostgreSQL project repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, and_, func, select
from sqlalchemy import delete as sql_delete
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import (
    Project,
    ProjectDocument,
    ProjectLink,
    ProjectMemory,
    ProjectResult,
)
from harness.storage.database import SessionFactory
from harness.storage.models import (
    InputArtifactRow,
    ProjectDocumentRow,
    ProjectLinkRow,
    ProjectMemoryRow,
    ProjectResultRow,
    ProjectRow,
)


def _payload(project: Project) -> dict[str, object]:
    payload = project.model_dump(mode="json", by_alias=True)
    if not project.local_skills:
        payload.pop("localSkills", None)
    return payload


def _project(row: ProjectRow) -> Project:
    return Project.model_validate(row.payload)


class PostgresProjectRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, project: Project) -> None:
        async with self._sessions() as session:
            session.add(
                ProjectRow(
                    project_id=project.project_id,
                    tenant_id=project.tenant_id,
                    user_id=project.user_id,
                    name=project.name,
                    pinned_at=project.pinned_at,
                    archived_at=project.archived_at,
                    created_at=project.created_at,
                    updated_at=project.updated_at,
                    payload=_payload(project),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"Project name already used: {project.name}") from error

    async def get(self, tenant_id: str, project_id: str) -> Project:
        async with self._sessions() as session:
            row = await session.get(ProjectRow, project_id)
            if row is None or row.tenant_id != tenant_id:
                raise NotFoundError(f"Project not found: {project_id}")
            return _project(row)

    async def list_for_user(
        self, tenant_id: str, user_id: str, *, include_archived: bool
    ) -> list[Project]:
        statement = select(ProjectRow).where(
            ProjectRow.tenant_id == tenant_id,
            ProjectRow.user_id == user_id,
        )
        if not include_archived:
            statement = statement.where(ProjectRow.archived_at.is_(None))
        statement = statement.order_by(
            ProjectRow.pinned_at.desc().nulls_last(),
            ProjectRow.created_at,
            ProjectRow.project_id,
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_project(row) for row in rows]

    async def replace(
        self, project: Project, *, expected_updated_at: datetime | None = None
    ) -> None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProjectRow)
                .where(
                    ProjectRow.project_id == project.project_id,
                    ProjectRow.tenant_id == project.tenant_id,
                )
                .with_for_update()
            )
            if row is None or row.tenant_id != project.tenant_id:
                raise NotFoundError(f"Project not found: {project.project_id}")
            if expected_updated_at is not None and row.updated_at != expected_updated_at:
                raise ConflictError("项目已被更新，请刷新后重试")
            row.name = project.name
            row.pinned_at = project.pinned_at
            row.archived_at = project.archived_at
            row.updated_at = project.updated_at
            row.payload = _payload(project)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"Project name already used: {project.name}") from error

    async def delete(self, tenant_id: str, project_id: str) -> None:
        statement = sql_delete(ProjectRow).where(
            ProjectRow.project_id == project_id,
            ProjectRow.tenant_id == tenant_id,
        )
        async with self._sessions() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            if not result.rowcount:
                await session.rollback()
                raise NotFoundError(f"Project not found: {project_id}")
            await session.commit()


def _memory(row: ProjectMemoryRow) -> ProjectMemory:
    return ProjectMemory(
        tenantId=row.tenant_id,
        userId=row.user_id,
        projectId=row.project_id,
        memoryId=row.memory_id,
        content=row.content,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )


def _document(row: ProjectDocumentRow, sha256: str | None = None) -> ProjectDocument:
    return ProjectDocument(
        tenantId=row.tenant_id,
        userId=row.user_id,
        projectId=row.project_id,
        inputArtifactId=row.input_artifact_id,
        name=row.name,
        mediaType=row.media_type,
        sizeBytes=row.size_bytes,
        sha256=sha256,
        createdAt=row.created_at,
    )


def _link(row: ProjectLinkRow) -> ProjectLink:
    return ProjectLink(
        tenantId=row.tenant_id,
        userId=row.user_id,
        projectId=row.project_id,
        linkId=row.link_id,
        url=row.url,
        title=row.title,
        createdAt=row.created_at,
    )


class PostgresProjectResourceRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def list_memories(self, tenant_id: str, project_id: str) -> list[ProjectMemory]:
        statement = (
            select(ProjectMemoryRow)
            .where(
                ProjectMemoryRow.tenant_id == tenant_id,
                ProjectMemoryRow.project_id == project_id,
            )
            .order_by(ProjectMemoryRow.created_at, ProjectMemoryRow.memory_id)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_memory(row) for row in rows]

    async def add_memory(self, memory: ProjectMemory) -> None:
        async with self._sessions() as session:
            session.add(
                ProjectMemoryRow(
                    memory_id=memory.memory_id,
                    tenant_id=memory.tenant_id,
                    user_id=memory.user_id,
                    project_id=memory.project_id,
                    content=memory.content,
                    created_at=memory.created_at,
                    updated_at=memory.updated_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"project memory already exists: {memory.memory_id}") from error

    async def get_memory(self, tenant_id: str, project_id: str, memory_id: str) -> ProjectMemory:
        async with self._sessions() as session:
            row = await session.get(ProjectMemoryRow, memory_id)
            if row is None or row.tenant_id != tenant_id or row.project_id != project_id:
                raise NotFoundError(f"project memory not found: {memory_id}")
            return _memory(row)

    async def replace_memory(self, memory: ProjectMemory) -> None:
        async with self._sessions() as session:
            row = await session.get(ProjectMemoryRow, memory.memory_id)
            if (
                row is None
                or row.tenant_id != memory.tenant_id
                or row.project_id != memory.project_id
            ):
                raise NotFoundError(f"project memory not found: {memory.memory_id}")
            row.content = memory.content
            row.updated_at = memory.updated_at
            await session.commit()

    async def delete_memory(self, tenant_id: str, project_id: str, memory_id: str) -> None:
        statement = sql_delete(ProjectMemoryRow).where(
            ProjectMemoryRow.tenant_id == tenant_id,
            ProjectMemoryRow.project_id == project_id,
            ProjectMemoryRow.memory_id == memory_id,
        )
        async with self._sessions() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            if not result.rowcount:
                await session.rollback()
                raise NotFoundError(f"project memory not found: {memory_id}")
            await session.commit()

    async def list_documents(self, tenant_id: str, project_id: str) -> list[ProjectDocument]:
        statement = (
            select(ProjectDocumentRow, InputArtifactRow.payload["sha256"].as_string())
            .outerjoin(
                InputArtifactRow,
                and_(
                    InputArtifactRow.tenant_id == ProjectDocumentRow.tenant_id,
                    InputArtifactRow.user_id == ProjectDocumentRow.user_id,
                    InputArtifactRow.input_artifact_id == ProjectDocumentRow.input_artifact_id,
                ),
            )
            .where(
                ProjectDocumentRow.tenant_id == tenant_id,
                ProjectDocumentRow.project_id == project_id,
            )
            .order_by(ProjectDocumentRow.created_at, ProjectDocumentRow.input_artifact_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [_document(row, sha256) for row, sha256 in rows]

    async def add_document(self, document: ProjectDocument) -> None:
        async with self._sessions() as session:
            session.add(
                ProjectDocumentRow(
                    tenant_id=document.tenant_id,
                    project_id=document.project_id,
                    input_artifact_id=document.input_artifact_id,
                    user_id=document.user_id,
                    name=document.name,
                    media_type=document.media_type,
                    size_bytes=document.size_bytes,
                    created_at=document.created_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(
                    f"document already attached: {document.input_artifact_id}"
                ) from error

    async def delete_document(
        self, tenant_id: str, project_id: str, input_artifact_id: str
    ) -> None:
        statement = sql_delete(ProjectDocumentRow).where(
            ProjectDocumentRow.tenant_id == tenant_id,
            ProjectDocumentRow.project_id == project_id,
            ProjectDocumentRow.input_artifact_id == input_artifact_id,
        )
        async with self._sessions() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            if not result.rowcount:
                await session.rollback()
                raise NotFoundError(f"project document not found: {input_artifact_id}")
            await session.commit()

    async def list_results(self, tenant_id: str, project_id: str) -> list[ProjectResult]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ProjectResultRow)
                    .where(
                        ProjectResultRow.tenant_id == tenant_id,
                        ProjectResultRow.project_id == project_id,
                    )
                    .order_by(ProjectResultRow.created_at.desc(), ProjectResultRow.version.desc())
                )
            ).all()
        return [ProjectResult.model_validate(row.payload) for row in rows]

    async def save_result(self, result: ProjectResult) -> ProjectResult:
        async with self._sessions() as session:
            # Serialize version allocation and duplicate saves across workers.
            project = await session.scalar(
                select(ProjectRow)
                .where(
                    ProjectRow.project_id == result.project_id,
                    ProjectRow.tenant_id == result.tenant_id,
                )
                .with_for_update()
            )
            if project is None:
                raise NotFoundError("Project not found")
            existing = await session.scalar(
                select(ProjectResultRow).where(
                    ProjectResultRow.tenant_id == result.tenant_id,
                    ProjectResultRow.project_id == result.project_id,
                    ProjectResultRow.artifact_id == result.artifact_id,
                )
            )
            if existing is not None:
                return ProjectResult.model_validate(existing.payload)
            latest_version = await session.scalar(
                select(func.max(ProjectResultRow.version)).where(
                    ProjectResultRow.tenant_id == result.tenant_id,
                    ProjectResultRow.project_id == result.project_id,
                    ProjectResultRow.name == result.name,
                )
            )
            version = (latest_version or 0) + 1
            saved = result.model_copy(update={"version": version})
            session.add(
                ProjectResultRow(
                    result_id=saved.result_id,
                    tenant_id=saved.tenant_id,
                    project_id=saved.project_id,
                    artifact_id=saved.artifact_id,
                    name=saved.name,
                    version=version,
                    created_at=saved.created_at,
                    payload=saved.model_dump(mode="json", by_alias=True),
                )
            )
            await session.commit()
            return saved

    async def finalize_result(
        self, tenant_id: str, project_id: str, result_id: str, finalized_at: datetime | None
    ) -> ProjectResult:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProjectResultRow)
                .where(
                    ProjectResultRow.result_id == result_id,
                    ProjectResultRow.tenant_id == tenant_id,
                    ProjectResultRow.project_id == project_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFoundError("Project result not found")
            saved = ProjectResult.model_validate(row.payload).model_copy(
                update={"finalized_at": finalized_at}
            )
            row.payload = saved.model_dump(mode="json", by_alias=True)
            await session.commit()
            return saved

    async def delete_for_project(self, tenant_id: str, project_id: str) -> None:
        async with self._sessions() as session:
            await session.execute(
                sql_delete(ProjectResultRow).where(
                    ProjectResultRow.tenant_id == tenant_id,
                    ProjectResultRow.project_id == project_id,
                )
            )
            await session.execute(
                sql_delete(ProjectMemoryRow).where(
                    ProjectMemoryRow.tenant_id == tenant_id,
                    ProjectMemoryRow.project_id == project_id,
                )
            )
            await session.execute(
                sql_delete(ProjectDocumentRow).where(
                    ProjectDocumentRow.tenant_id == tenant_id,
                    ProjectDocumentRow.project_id == project_id,
                )
            )
            await session.execute(
                sql_delete(ProjectLinkRow).where(
                    ProjectLinkRow.tenant_id == tenant_id,
                    ProjectLinkRow.project_id == project_id,
                )
            )
            await session.commit()

    async def list_links(self, tenant_id: str, project_id: str) -> list[ProjectLink]:
        statement = (
            select(ProjectLinkRow)
            .where(
                ProjectLinkRow.tenant_id == tenant_id,
                ProjectLinkRow.project_id == project_id,
            )
            .order_by(ProjectLinkRow.created_at, ProjectLinkRow.link_id)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_link(row) for row in rows]

    async def add_link(self, link: ProjectLink) -> None:
        async with self._sessions() as session:
            session.add(
                ProjectLinkRow(
                    link_id=link.link_id,
                    tenant_id=link.tenant_id,
                    user_id=link.user_id,
                    project_id=link.project_id,
                    url=link.url,
                    title=link.title,
                    created_at=link.created_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"project link already exists: {link.link_id}") from error

    async def delete_link(self, tenant_id: str, project_id: str, link_id: str) -> None:
        statement = sql_delete(ProjectLinkRow).where(
            ProjectLinkRow.tenant_id == tenant_id,
            ProjectLinkRow.project_id == project_id,
            ProjectLinkRow.link_id == link_id,
        )
        async with self._sessions() as session:
            result = cast(CursorResult[Any], await session.execute(statement))
            if not result.rowcount:
                await session.rollback()
                raise NotFoundError(f"project link not found: {link_id}")
            await session.commit()
