"""Project contracts: a user-owned container that groups tasks."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from harness.studio.models import StudioModel

MAX_PROJECT_NAME = 120


class Project(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class CreateProjectRequest(StudioModel):
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)


class UpdateProjectRequest(StudioModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_PROJECT_NAME)
    archived: bool | None = None
