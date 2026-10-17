"""Project contracts: a user-owned container that groups tasks."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from harness.studio.models import StudioModel

MAX_PROJECT_NAME = 120
MAX_PROJECT_INSTRUCTIONS = 20_000
MAX_PROJECT_MEMORY_CONTENT = 4_000
MAX_PROJECT_MEMORIES = 200
MAX_PROJECT_DOCUMENTS = 10


class Project(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)
    # Project-scoped context shared by every task in the project. Each Run
    # captures it when the Run is created, so editing the project later never
    # rewrites the instructions an older Run already executed with.
    instructions: str = Field(default="", max_length=MAX_PROJECT_INSTRUCTIONS)
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ProjectMemory(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    project_id: str = Field(alias="projectId")
    memory_id: str = Field(alias="memoryId")
    content: str = Field(min_length=1, max_length=MAX_PROJECT_MEMORY_CONTENT)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ProjectDocument(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    project_id: str = Field(alias="projectId")
    input_artifact_id: str = Field(alias="inputArtifactId")
    name: str = Field(min_length=1, max_length=500)
    media_type: str = Field(default="application/octet-stream", alias="mediaType")
    size_bytes: int = Field(default=0, alias="sizeBytes", ge=0)
    created_at: datetime = Field(alias="createdAt")


class ProjectResources(StudioModel):
    project: Project
    memories: tuple[ProjectMemory, ...] = ()
    documents: tuple[ProjectDocument, ...] = ()


class CreateProjectRequest(StudioModel):
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)
    instructions: str | None = Field(default=None, max_length=MAX_PROJECT_INSTRUCTIONS)


class UpdateProjectRequest(StudioModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_PROJECT_NAME)
    instructions: str | None = Field(default=None, max_length=MAX_PROJECT_INSTRUCTIONS)
    archived: bool | None = None


class CreateProjectMemoryRequest(StudioModel):
    content: str = Field(min_length=1, max_length=MAX_PROJECT_MEMORY_CONTENT)


class UpdateProjectMemoryRequest(CreateProjectMemoryRequest):
    pass


class AttachProjectDocumentRequest(StudioModel):
    input_artifact_id: str = Field(alias="inputArtifactId", min_length=1, max_length=128)


class ProjectRunContext(StudioModel):
    """Snapshot of project resources captured when one Run is created."""

    project_id: str = Field(alias="projectId")
    text: str = ""
    document_ids: tuple[str, ...] = Field(default=(), alias="documentIds")
    memory_count: int = Field(default=0, alias="memoryCount")
    document_count: int = Field(default=0, alias="documentCount")
