from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MemoryModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class MemoryStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    DELETED = "deleted"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class MemoryType(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    ENTITY = "entity"
    DECISION = "decision"


class MemorySensitivity(StrEnum):
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    PROHIBITED = "prohibited"


class MemorySourceKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    IMPORT = "import"


class ConsentMode(StrEnum):
    PER_ENTRY = "per_entry"
    AGENT_POLICY = "agent_policy"


class MemorySource(MemoryModel):
    source_id: str = Field(alias="sourceId")
    kind: MemorySourceKind
    label: str
    run_id: str | None = Field(default=None, alias="runId")
    session_id: str | None = Field(default=None, alias="sessionId")
    captured_at: datetime = Field(alias="capturedAt")
    evidence: str = Field(default="", max_length=1000)
    extraction_job_id: str | None = Field(default=None, alias="extractionJobId")
    extraction_version: int | None = Field(default=None, alias="extractionVersion")


class MemoryConsent(MemoryModel):
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    agent_name: str = Field(alias="agentName")
    consent_id: str = Field(alias="consentId")
    mode: ConsentMode
    allow_agent_personal: bool = Field(alias="allowAgentPersonal")
    version: int = Field(ge=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    revoked_at: datetime | None = Field(default=None, alias="revokedAt")

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class MemoryRetention(MemoryModel):
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    agent_name: str = Field(alias="agentName")
    default_days: int = Field(default=180, alias="defaultDays", ge=1, le=3650)
    max_days: int = Field(default=365, alias="maxDays", ge=1, le=3650)
    version: int = Field(default=1, ge=1)
    updated_at: datetime = Field(alias="updatedAt")


class MemoryEntry(MemoryModel):
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    agent_name: str = Field(alias="agentName")
    entry_id: str = Field(alias="entryId")
    content: str
    content_hash: str = Field(alias="contentHash")
    sensitivity: MemorySensitivity
    status: MemoryStatus
    version: int = Field(ge=1)
    confidence: float = Field(ge=0, le=1)
    source: MemorySource
    consent_id: str | None = Field(default=None, alias="consentId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    deleted_at: datetime | None = Field(default=None, alias="deletedAt")
    agent_owner_user_id: str | None = Field(default=None, alias="agentOwnerUserId")
    memory_type: MemoryType = Field(default=MemoryType.FACT, alias="memoryType")
    topic: str = Field(default="", max_length=120)
    conditions: str = Field(default="", max_length=500)
    supersedes: str | None = None
    supersedes_version: int | None = Field(default=None, alias="supersedesVersion")

    @property
    def owner_id(self) -> str:
        return self.agent_owner_user_id or self.user_id


class MemorySearchHit(MemoryModel):
    entry: MemoryEntry
    score: float = Field(ge=0, le=1)
    matched_terms: tuple[str, ...] = Field(default=(), alias="matchedTerms")


class ProposeMemoryRequest(MemoryModel):
    agent_name: str = Field(alias="agentName", min_length=1, max_length=128)
    agent_owner_user_id: str | None = Field(default=None, alias="agentOwnerUserId", max_length=128)
    content: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(default=0.7, ge=0, le=1)
    source_kind: MemorySourceKind = Field(default=MemorySourceKind.USER, alias="sourceKind")
    source_label: str = Field(default="用户提交", alias="sourceLabel", max_length=200)
    run_id: str | None = Field(default=None, alias="runId", max_length=128)
    session_id: str | None = Field(default=None, alias="sessionId", max_length=128)
    memory_type: MemoryType = Field(default=MemoryType.FACT, alias="memoryType")
    topic: str = Field(default="", max_length=120)
    conditions: str = Field(default="", max_length=500)


class MemoryVersionRequest(MemoryModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)


class UpdateMemoryRequest(MemoryVersionRequest):
    content: str = Field(min_length=1, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class SearchMemoryRequest(MemoryModel):
    agent_name: str = Field(alias="agentName", min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=50)
    agent_owner_user_id: str | None = Field(default=None, alias="agentOwnerUserId")


class ReplaceConsentRequest(MemoryModel):
    expected_version: int = Field(alias="expectedVersion", ge=0)
    allow_agent_personal: bool = Field(alias="allowAgentPersonal")


class ReplaceRetentionRequest(MemoryModel):
    expected_version: int = Field(alias="expectedVersion", ge=0)
    default_days: int = Field(alias="defaultDays", ge=1, le=3650)
    max_days: int = Field(alias="maxDays", ge=1, le=3650)
