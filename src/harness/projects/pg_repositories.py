"""PostgreSQL project repository."""

from __future__ import annotations

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import Project
from harness.storage.database import SessionFactory
from harness.storage.models import ProjectRow


def _payload(project: Project) -> dict[str, object]:
    return project.model_dump(mode="json", by_alias=True)


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
                raise ConflictError(
                    f"Project name already used: {project.name}"
                ) from error

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
        statement = statement.order_by(ProjectRow.created_at, ProjectRow.project_id)
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [_project(row) for row in rows]

    async def replace(self, project: Project) -> None:
        async with self._sessions() as session:
            row = await session.get(ProjectRow, project.project_id)
            if row is None or row.tenant_id != project.tenant_id:
                raise NotFoundError(f"Project not found: {project.project_id}")
            row.name = project.name
            row.archived_at = project.archived_at
            row.updated_at = project.updated_at
            row.payload = _payload(project)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(
                    f"Project name already used: {project.name}"
                ) from error

    async def delete(self, tenant_id: str, project_id: str) -> None:
        statement = sql_delete(ProjectRow).where(
            ProjectRow.project_id == project_id,
            ProjectRow.tenant_id == tenant_id,
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            if not result.rowcount:
                await session.rollback()
                raise NotFoundError(f"Project not found: {project_id}")
            await session.commit()
