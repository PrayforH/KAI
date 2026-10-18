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
MAX_PROJECT_CONNECTORS = 20
MAX_PROJECT_SKILLS = 20
MAX_PROJECT_KNOWLEDGE_BASES = 10
MAX_PROJECT_LINKS = 20
MAX_PROJECT_LINK_URL = 2_048
MAX_PROJECT_LINK_TITLE = 200


class Project(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)
    # Project-scoped context shared by every task in the project. Each Run
    # captures it when the Run is created, so editing the project later never
    # rewrites the instructions an older Run already executed with.
    instructions: str = Field(default="", max_length=MAX_PROJECT_INSTRUCTIONS)
    # Platform Skill packages selected for this project, pinned as
    # ``packageId@revision`` so a later catalog revision cannot change an
    # already-created Run. Files are staged per Run, not copied into the Agent.
    skill_references: tuple[str, ...] = Field(default=(), alias="skillReferences")
    # Knowledge bases pinned to the project. Every Run inherits them; a
    # per-task selection in the composer is added on top rather than replacing
    # the project's own binding.
    knowledge_references: tuple[str, ...] = Field(default=(), alias="knowledgeReferences")
    mcp_references: tuple[str, ...] = Field(default=(), alias="mcpReferences")
    pinned_at: datetime | None = Field(default=None, alias="pinnedAt")
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
    sha256: str | None = None
    created_at: datetime = Field(alias="createdAt")


class ProjectLink(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    project_id: str = Field(alias="projectId")
    link_id: str = Field(alias="linkId")
    url: str = Field(max_length=MAX_PROJECT_LINK_URL)
    title: str = Field(default="", max_length=MAX_PROJECT_LINK_TITLE)
    created_at: datetime = Field(alias="createdAt")


class ProjectResources(StudioModel):
    project: Project
    memories: tuple[ProjectMemory, ...] = ()
    documents: tuple[ProjectDocument, ...] = ()
    links: tuple[ProjectLink, ...] = ()


class CreateProjectRequest(StudioModel):
    name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME)
    instructions: str | None = Field(default=None, max_length=MAX_PROJECT_INSTRUCTIONS)
    skill_references: tuple[str, ...] | None = Field(
        default=None, alias="skillReferences", max_length=MAX_PROJECT_SKILLS
    )
    knowledge_references: tuple[str, ...] | None = Field(
        default=None, alias="knowledgeReferences", max_length=MAX_PROJECT_KNOWLEDGE_BASES
    )


class UpdateProjectRequest(StudioModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_PROJECT_NAME)
    instructions: str | None = Field(default=None, max_length=MAX_PROJECT_INSTRUCTIONS)
    skill_references: tuple[str, ...] | None = Field(
        default=None, alias="skillReferences", max_length=MAX_PROJECT_SKILLS
    )
    knowledge_references: tuple[str, ...] | None = Field(
        default=None, alias="knowledgeReferences", max_length=MAX_PROJECT_KNOWLEDGE_BASES
    )
    mcp_references: tuple[str, ...] | None = Field(
        default=None, alias="mcpReferences", max_length=MAX_PROJECT_CONNECTORS
    )
    pinned: bool | None = None
    archived: bool | None = None


class CreateProjectMemoryRequest(StudioModel):
    content: str = Field(min_length=1, max_length=MAX_PROJECT_MEMORY_CONTENT)


class UpdateProjectMemoryRequest(CreateProjectMemoryRequest):
    pass


class AttachProjectDocumentRequest(StudioModel):
    input_artifact_id: str = Field(alias="inputArtifactId", min_length=1, max_length=128)


class CreateProjectLinkRequest(StudioModel):
    url: str = Field(min_length=1, max_length=MAX_PROJECT_LINK_URL)
    title: str | None = Field(default=None, max_length=MAX_PROJECT_LINK_TITLE)


class ProjectRunContext(StudioModel):
    """Snapshot of project resources captured when one Run is created."""

    project_id: str = Field(alias="projectId")
    text: str = ""
    resource_manifest: dict[str, object] = Field(default_factory=dict, alias="resourceManifest")
    document_ids: tuple[str, ...] = Field(default=(), alias="documentIds")
    skill_references: tuple[str, ...] = Field(default=(), alias="skillReferences")
    knowledge_references: tuple[str, ...] = Field(default=(), alias="knowledgeReferences")
    mcp_references: tuple[str, ...] = Field(default=(), alias="mcpReferences")
    memory_count: int = Field(default=0, alias="memoryCount")
    document_count: int = Field(default=0, alias="documentCount")
    skill_count: int = Field(default=0, alias="skillCount")
    knowledge_count: int = Field(default=0, alias="knowledgeCount")
    link_count: int = Field(default=0, alias="linkCount")


class ProjectResult(StudioModel):
    """A saved immutable file snapshot, independent of task membership."""

    result_id: str = Field(alias="resultId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    project_id: str = Field(alias="projectId")
    artifact_id: str = Field(alias="artifactId")
    input_artifact_id: str = Field(alias="inputArtifactId")
    thread_id: str = Field(alias="threadId")
    thread_title: str = Field(alias="threadTitle")
    run_id: str = Field(alias="runId")
    name: str
    media_type: str = Field(alias="mediaType")
    size_bytes: int = Field(alias="sizeBytes")
    sha256: str | None = None
    version: int = Field(default=1, ge=1)
    finalized_at: datetime | None = Field(default=None, alias="finalizedAt")
    created_at: datetime = Field(alias="createdAt")


class SaveProjectResultRequest(StudioModel):
    artifact_id: str = Field(alias="artifactId", min_length=1, max_length=128)


class FinalizeProjectResultRequest(StudioModel):
    finalized: bool
