from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

KnowledgeReference = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
    )


class KnowledgeSourceKind(StrEnum):
    FILE = "file"
    WEB = "web"
    WEKNORA = "weknora"


class KnowledgeBaseType(StrEnum):
    """Product-level knowledge base flavor rendered as cards in the console."""

    RAG = "rag"
    WIKI = "wiki"
    HYBRID = "hybrid"


class KnowledgeBaseEngine(StrEnum):
    """Which retrieval backend owns a knowledge base's documents and indexes."""

    LEGACY = "legacy"
    WEKNORA = "weknora"


class KnowledgeMemberRole(StrEnum):
    """Per-member knowledge base permission: read-only or read-write."""

    VIEWER = "viewer"
    EDITOR = "editor"

    @property
    def rank(self) -> int:
        return 1 if self is KnowledgeMemberRole.VIEWER else 2


class KnowledgeMemberSubject(StrEnum):
    """Who a membership row grants to.

    ``org_unit`` (and the inherited ``org_path``) is reserved for the phase-2
    IDAAS organization-tree rollout; phase 1 only writes ``user`` rows.
    """

    USER = "user"
    ORG_UNIT = "org_unit"


class KnowledgeSourceHealth(StrEnum):
    PENDING = "pending"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class KnowledgeSyncStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    UNCHANGED = "unchanged"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in {
            KnowledgeSyncStatus.SUCCEEDED,
            KnowledgeSyncStatus.UNCHANGED,
            KnowledgeSyncStatus.FAILED,
        }


class KnowledgeVisibility(StrEnum):
    TENANT = "tenant"
    RESTRICTED = "restricted"


class KnowledgeResultTrust(StrEnum):
    SENSITIVE = "sensitive"
    UNTRUSTED = "untrusted"


class KnowledgeAcl(KnowledgeModel):
    visibility: KnowledgeVisibility = KnowledgeVisibility.TENANT
    user_ids: tuple[str, ...] = Field(default=(), alias="userIds")
    workload_ids: tuple[str, ...] = Field(default=(), alias="workloadIds")

    @model_validator(mode="after")
    def valid_acl(self) -> KnowledgeAcl:
        if len(set(self.user_ids)) != len(self.user_ids):
            raise ValueError("knowledge ACL contains duplicate user IDs")
        if len(set(self.workload_ids)) != len(self.workload_ids):
            raise ValueError("knowledge ACL contains duplicate workload IDs")
        if any(not item.strip() for item in (*self.user_ids, *self.workload_ids)):
            raise ValueError("knowledge ACL identifiers must be non-empty")
        if (
            self.visibility is KnowledgeVisibility.RESTRICTED
            and not self.user_ids
            and not self.workload_ids
        ):
            raise ValueError("restricted knowledge ACL must allow an identity")
        return self

    def allows(self, actor_id: str) -> bool:
        if self.visibility is KnowledgeVisibility.TENANT:
            return True
        if actor_id.startswith("trigger:"):
            return actor_id in self.workload_ids
        return actor_id in self.user_ids


class KnowledgeInputDocument(KnowledgeModel):
    document_id: str = Field(alias="documentId", min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    source_uri: str | None = Field(default=None, alias="sourceUri", max_length=2_000)


class FileKnowledgeConfig(KnowledgeModel):
    type: Literal["file"] = "file"
    documents: tuple[KnowledgeInputDocument, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_document_ids(self) -> FileKnowledgeConfig:
        identifiers = [item.document_id for item in self.documents]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("file knowledge documents must have unique IDs")
        return self


class WebKnowledgeConfig(KnowledgeModel):
    type: Literal["web"] = "web"
    url: AnyHttpUrl
    title: str | None = Field(default=None, max_length=500)
    max_bytes: int = Field(
        default=2 * 1024 * 1024,
        alias="maxBytes",
        ge=1_024,
        le=10 * 1024 * 1024,
    )


class WeknoraKnowledgeConfig(KnowledgeModel):
    """Connector config for a knowledge source backed by a WeKnora base."""

    type: Literal["weknora"] = "weknora"
    weknora_base_id: str = Field(alias="weknoraBaseId", min_length=1, max_length=128)


KnowledgeSourceConfig = Annotated[
    FileKnowledgeConfig | WebKnowledgeConfig | WeknoraKnowledgeConfig,
    Field(discriminator="type"),
]


class KnowledgeBase(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    reference: KnowledgeReference
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    source_references: tuple[KnowledgeReference, ...] = Field(
        default=(),
        alias="sourceReferences",
    )
    kb_type: KnowledgeBaseType = Field(
        default=KnowledgeBaseType.RAG,
        alias="kbType",
    )
    engine: KnowledgeBaseEngine = Field(
        default=KnowledgeBaseEngine.LEGACY,
        alias="engine",
    )
    engine_ref: str = Field(default="", alias="engineRef", max_length=128)
    # Last synced document count for engine-backed bases; 0 when unknown.
    document_count: int = Field(default=0, alias="documentCount", ge=0)
    revision: int = Field(ge=1)
    created_by: str = Field(alias="createdBy", min_length=1)
    updated_by: str = Field(alias="updatedBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @model_validator(mode="after")
    def unique_sources(self) -> KnowledgeBase:
        if len(set(self.source_references)) != len(self.source_references):
            raise ValueError("Knowledge Base contains duplicate source references")
        return self


class KnowledgeSource(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    reference: KnowledgeReference
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    kind: KnowledgeSourceKind
    config: KnowledgeSourceConfig
    acl: KnowledgeAcl = KnowledgeAcl()
    revision: int = Field(ge=1)
    health: KnowledgeSourceHealth
    active_snapshot_id: str | None = Field(default=None, alias="activeSnapshotId")
    checkpoint: dict[str, str | int] = Field(default_factory=dict)
    last_sync_id: str | None = Field(default=None, alias="lastSyncId")
    last_sync_at: datetime | None = Field(default=None, alias="lastSyncAt")
    last_error: str | None = Field(default=None, alias="lastError", max_length=1_000)
    created_by: str = Field(alias="createdBy", min_length=1)
    updated_by: str = Field(alias="updatedBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @model_validator(mode="after")
    def config_matches_kind(self) -> KnowledgeSource:
        if self.kind.value != self.config.type:
            raise ValueError("knowledge source kind does not match connector config")
        # Engine-backed sources keep documents and chunks inside the remote
        # engine, so a healthy WeKnora source legitimately has no local
        # snapshot. Re-validating the stored payload would otherwise reject it.
        if (
            self.kind is not KnowledgeSourceKind.WEKNORA
            and self.health is KnowledgeSourceHealth.HEALTHY
            and self.active_snapshot_id is None
        ):
            raise ValueError("healthy knowledge source requires an active snapshot")
        return self

    @property
    def result_trust(self) -> KnowledgeResultTrust:
        if self.kind is KnowledgeSourceKind.WEB:
            return KnowledgeResultTrust.UNTRUSTED
        return KnowledgeResultTrust.SENSITIVE


class KnowledgeSourceSummary(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    reference: KnowledgeReference
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    kind: KnowledgeSourceKind
    visibility: KnowledgeVisibility
    revision: int = Field(ge=1)
    health: KnowledgeSourceHealth
    active_snapshot_id: str | None = Field(default=None, alias="activeSnapshotId")
    last_sync_id: str | None = Field(default=None, alias="lastSyncId")
    last_sync_at: datetime | None = Field(default=None, alias="lastSyncAt")
    last_error: str | None = Field(default=None, alias="lastError", max_length=1_000)
    created_by: str = Field(alias="createdBy", min_length=1)
    updated_by: str = Field(alias="updatedBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    @classmethod
    def from_source(cls, source: KnowledgeSource) -> KnowledgeSourceSummary:
        return cls(
            tenantId=source.tenant_id,
            reference=source.reference,
            displayName=source.display_name,
            description=source.description,
            kind=source.kind,
            visibility=source.acl.visibility,
            revision=source.revision,
            health=source.health,
            activeSnapshotId=source.active_snapshot_id,
            lastSyncId=source.last_sync_id,
            lastSyncAt=source.last_sync_at,
            lastError=source.last_error,
            createdBy=source.created_by,
            updatedBy=source.updated_by,
            createdAt=source.created_at,
            updatedAt=source.updated_at,
        )


class ConnectorDocument(KnowledgeModel):
    document_id: str = Field(alias="documentId", min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    source_uri: str = Field(alias="sourceUri", min_length=1, max_length=2_000)
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class ConnectorSyncResult(KnowledgeModel):
    documents: tuple[ConnectorDocument, ...]
    checkpoint: dict[str, str | int]


class KnowledgeCitation(KnowledgeModel):
    knowledge_base_reference: KnowledgeReference = Field(alias="knowledgeBaseReference")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    source_display_name: str = Field(alias="sourceDisplayName")
    snapshot_id: str = Field(alias="snapshotId")
    document_id: str = Field(alias="documentId")
    chunk_id: str = Field(alias="chunkId")
    title: str
    uri: str


class KnowledgeChunk(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId")
    snapshot_id: str = Field(alias="snapshotId")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    chunk_id: str = Field(alias="chunkId")
    document_id: str = Field(alias="documentId")
    ordinal: int = Field(ge=0)
    title: str
    source_uri: str = Field(alias="sourceUri")
    content: str
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    token_terms: tuple[str, ...] = Field(alias="tokenTerms")
    created_at: datetime = Field(alias="createdAt")


class KnowledgeSnapshot(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId")
    snapshot_id: str = Field(alias="snapshotId")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    source_revision: int = Field(alias="sourceRevision", ge=1)
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    document_count: int = Field(alias="documentCount", ge=1)
    chunk_count: int = Field(alias="chunkCount", ge=1)
    checkpoint: dict[str, str | int]
    created_at: datetime = Field(alias="createdAt")

    @classmethod
    def digest_chunks(cls, chunks: tuple[KnowledgeChunk, ...]) -> str:
        payload = [
            {
                "chunkId": item.chunk_id,
                "documentId": item.document_id,
                "ordinal": item.ordinal,
                "title": item.title,
                "sourceUri": item.source_uri,
                "contentHash": item.content_hash,
            }
            for item in sorted(chunks, key=lambda value: value.chunk_id)
        ]
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class KnowledgeSyncRun(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId")
    sync_id: str = Field(alias="syncId")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    source_revision: int = Field(alias="sourceRevision", ge=1)
    status: KnowledgeSyncStatus
    checkpoint_before: dict[str, str | int] = Field(alias="checkpointBefore")
    checkpoint_after: dict[str, str | int] = Field(
        default_factory=dict,
        alias="checkpointAfter",
    )
    snapshot_id: str | None = Field(default=None, alias="snapshotId")
    documents_seen: int = Field(default=0, alias="documentsSeen", ge=0)
    chunks_written: int = Field(default=0, alias="chunksWritten", ge=0)
    error_code: str | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(
        default=None,
        alias="errorMessage",
        max_length=1_000,
    )
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")


class KnowledgeSearchHit(KnowledgeModel):
    content: str
    score: float = Field(ge=0, le=1)
    trust: KnowledgeResultTrust
    citation: KnowledgeCitation
    matched_terms: tuple[str, ...] = Field(alias="matchedTerms")


class KnowledgeSnapshotBinding(KnowledgeModel):
    knowledge_base_reference: KnowledgeReference = Field(alias="knowledgeBaseReference")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    snapshot_id: str = Field(alias="snapshotId")
    trust: KnowledgeResultTrust


class CreateKnowledgeBaseRequest(KnowledgeModel):
    reference: KnowledgeReference
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    source_references: tuple[KnowledgeReference, ...] = Field(
        default=(),
        alias="sourceReferences",
    )
    kb_type: KnowledgeBaseType = Field(
        default=KnowledgeBaseType.RAG,
        alias="kbType",
    )
    engine: KnowledgeBaseEngine = Field(
        default=KnowledgeBaseEngine.LEGACY,
        alias="engine",
    )


class KnowledgeDocumentStatus(KnowledgeModel):
    """Mirror of a WeKnora document's ingestion state for the console."""

    tenant_id: str = Field(alias="tenantId")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    document_id: str = Field(alias="documentId")
    title: str
    parse_status: str = Field(alias="parseStatus")
    summary_status: str = Field(alias="summaryStatus")
    file_type: str = Field(default="", alias="fileType")
    file_size: int = Field(default=0, alias="fileSize", ge=0)
    enabled: bool = True
    created_at: str = Field(default="", alias="createdAt")
    description: str = Field(default="", alias="description")


class KnowledgeWikiPage(KnowledgeModel):
    knowledge_base_reference: str | None = Field(default=None, alias="knowledgeBaseReference")
    slug: str
    title: str = Field(min_length=1)
    page_type: str = Field(alias="pageType", min_length=1)
    content: str = ""
    summary: str = ""
    aliases: tuple[str, ...] = Field(default=())
    category_path: tuple[str, ...] = Field(default=(), alias="categoryPath")
    folder_id: str = Field(default="", alias="folderId")


class KnowledgeWikiGraphNode(KnowledgeModel):
    slug: str
    title: str
    page_type: str = Field(alias="pageType")
    link_count: int = Field(default=0, alias="linkCount", ge=0)


class KnowledgeWikiGraph(KnowledgeModel):
    nodes: tuple[KnowledgeWikiGraphNode, ...]
    links: tuple[tuple[str, str], ...]


class KnowledgeWikiStats(KnowledgeModel):
    total_pages: int = Field(alias="totalPages", ge=0)
    pages_by_type: dict[str, int] = Field(alias="pagesByType")
    total_links: int = Field(default=0, alias="totalLinks", ge=0)


class KnowledgeBaseMember(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    member_id: str = Field(alias="memberId", min_length=1)
    knowledge_base_reference: KnowledgeReference = Field(alias="knowledgeBaseReference")
    subject_type: KnowledgeMemberSubject = Field(alias="subjectType")
    subject_id: str = Field(alias="subjectId", min_length=1, max_length=320)
    org_path: str = Field(default="", alias="orgPath", max_length=1_000)
    role: KnowledgeMemberRole
    display_name: str = Field(default="", alias="displayName", max_length=160)
    email: str = Field(default="", max_length=320)
    granted_by: str = Field(alias="grantedBy", min_length=1)
    granted_at: datetime = Field(alias="grantedAt")


class AddKnowledgeMembersRequest(KnowledgeModel):
    """Batch grant. Phase 1 accepts user ids or emails; emails are resolved
    against the platform user directory and unresolved entries are reported."""

    user_ids: tuple[str, ...] = Field(default=(), alias="userIds")
    emails: tuple[str, ...] = Field(default=())
    role: KnowledgeMemberRole = KnowledgeMemberRole.VIEWER

    @model_validator(mode="after")
    def require_subject(self) -> AddKnowledgeMembersRequest:
        if not self.user_ids and not self.emails:
            raise ValueError("at least one user id or email is required")
        return self


class AddKnowledgeMembersResult(KnowledgeModel):
    members: tuple[KnowledgeBaseMember, ...]
    unresolved: tuple[str, ...] = ()


class UpdateKnowledgeMemberRequest(KnowledgeModel):
    role: KnowledgeMemberRole


class KnowledgeDocumentTable(KnowledgeModel):
    """A spreadsheet document rendered as a cell grid for the console."""

    tenant_id: str = Field(alias="tenantId")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    document_id: str = Field(alias="documentId")
    title: str
    sheet: str = ""
    rows: tuple[tuple[str, ...], ...] = ()
    truncated: bool = False
    extra_sheets: tuple[str, ...] = Field(default=(), alias="extraSheets")


class CreateKnowledgeDocumentRequest(KnowledgeModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=2 * 1024 * 1024)


class KnowledgeDocumentChunk(KnowledgeModel):
    tenant_id: str = Field(alias="tenantId")
    source_reference: KnowledgeReference = Field(alias="sourceReference")
    document_id: str = Field(alias="documentId")
    chunk_id: str = Field(alias="chunkId")
    title: str
    content: str
    seq: int = Field(ge=0)


class ReplaceKnowledgeBaseRequest(KnowledgeModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    source_references: tuple[KnowledgeReference, ...] = Field(
        default=(),
        alias="sourceReferences",
    )


class CreateKnowledgeSourceRequest(KnowledgeModel):
    reference: KnowledgeReference
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    kind: KnowledgeSourceKind
    config: KnowledgeSourceConfig
    acl: KnowledgeAcl = KnowledgeAcl()
    sync_now: bool = Field(default=True, alias="syncNow")


class CreateKnowledgeSourceResult(KnowledgeModel):
    source: KnowledgeSourceSummary
    sync: KnowledgeSyncRun | None = None


class ReplaceKnowledgeSourceRequest(KnowledgeModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    description: str = Field(default="", max_length=1_000)
    config: KnowledgeSourceConfig
    acl: KnowledgeAcl = KnowledgeAcl()
    enabled: bool = True


class SearchKnowledgeRequest(KnowledgeModel):
    query: str = Field(min_length=1, max_length=1_000)
    knowledge_base_references: tuple[KnowledgeReference, ...] = Field(
        alias="knowledgeBaseReferences",
        min_length=1,
    )
    limit: int = Field(default=8, ge=1, le=25)


class SearchKnowledgeResponse(KnowledgeModel):
    hits: tuple[KnowledgeSearchHit, ...]
    searched_snapshot_ids: tuple[str, ...] = Field(alias="searchedSnapshotIds")
