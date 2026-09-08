"""Immutable, content-addressed context recovery facts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.policy.models import ContextTrust

_SHA256_PATTERN = r"^sha256:[a-f0-9]{64}$"


def context_digest_content_hash(payload: dict[str, Any]) -> str:
    def json_default(value: object) -> str:
        if isinstance(value, datetime):
            normalized = value.isoformat()
            return (
                normalized.replace("+00:00", "Z")
                if value.utcoffset() == timedelta(0)
                else normalized
            )
        raise TypeError(f"unsupported context digest value: {type(value).__name__}")

    encoded = json.dumps(
        payload,
        default=json_default,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ContextModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class ContextBudgetLevel(StrEnum):
    """Provider-window risk without implying that a rebase happened automatically."""

    GREEN = "green"
    WATCH = "watch"
    COMPACT_READY = "compact_ready"
    EMERGENCY = "emergency"


class ContextWindowCategory(ContextModel):
    name: str = Field(min_length=1, max_length=128)
    tokens: int = Field(ge=0)


class ContextWindowSnapshot(ContextModel):
    """Latest content-free provider window observation and its policy evaluation."""

    source_run_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    phase: str = Field(min_length=1, max_length=32)
    total_tokens: int = Field(ge=0)
    max_tokens: int = Field(ge=0)
    raw_max_tokens: int = Field(ge=0)
    headroom_tokens: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)
    model: str = Field(max_length=256)
    auto_compact_enabled: bool
    auto_compact_threshold: int | None = Field(default=None, ge=0)
    provider_threshold_percentage: float | None = Field(default=None, ge=0, le=100)
    categories: tuple[ContextWindowCategory, ...] = Field(default=(), max_length=64)
    level: ContextBudgetLevel
    soft_threshold_percentage: float = Field(ge=0, le=100)
    compact_ready_percentage: float = Field(ge=0, le=100)
    hard_threshold_percentage: float = Field(ge=0, le=100)
    recommended_action: Literal[
        "none",
        "reduce_optional_context",
        "consider_rebase",
        "rebase_now",
    ]

    @model_validator(mode="after")
    def valid_threshold_order(self) -> ContextWindowSnapshot:
        if not (
            self.soft_threshold_percentage
            <= self.compact_ready_percentage
            <= self.hard_threshold_percentage
        ):
            raise ValueError("context window thresholds must be monotonic")
        if self.max_tokens and self.total_tokens + self.headroom_tokens != self.max_tokens:
            raise ValueError("context window headroom must match the provider maximum")
        return self


class ContextWindowAvailability(ContextModel):
    """Whether the runtime could obtain an exact provider-window observation."""

    status: Literal["pending", "available", "unavailable"]
    checked_at: datetime | None = None
    source_run_id: str | None = Field(default=None, max_length=128)
    reason: Literal["control_timeout", "control_unavailable"] | None = None

    @model_validator(mode="after")
    def complete_outcome(self) -> ContextWindowAvailability:
        if self.status == "pending":
            if (
                self.checked_at is not None
                or self.source_run_id is not None
                or self.reason is not None
            ):
                raise ValueError("pending context window status cannot have an outcome")
            return self
        if self.checked_at is None or self.source_run_id is None:
            raise ValueError("context window outcome requires a source Run and timestamp")
        if self.status == "available" and self.reason is not None:
            raise ValueError("available context window status cannot have an error reason")
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("unavailable context window status requires a reason")
        return self


class ContextDigestSource(ContextModel):
    sdk_session_id_hash: str = Field(pattern=_SHA256_PATTERN)
    through_run_id: str = Field(min_length=1, max_length=128)
    through_event_sequence: int = Field(ge=0)
    transcript_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)


class ContextDigestEntry(ContextModel):
    """A short data fact whose provenance remains independently inspectable."""

    text: str = Field(min_length=1, max_length=1_000)
    source_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    trust: ContextTrust

    @model_validator(mode="after")
    def unique_sources(self) -> ContextDigestEntry:
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("context digest source references must be unique")
        if any(not value.strip() for value in self.source_refs):
            raise ValueError("context digest source references must be non-empty")
        return self


class ContextDigestObjectRef(ContextModel):
    """Reference to durable content; the content itself never enters the Digest."""

    ref: str = Field(min_length=1, max_length=256)
    content_hash: str = Field(pattern=_SHA256_PATTERN)
    title: str = Field(min_length=1, max_length=500)
    media_type: str | None = Field(default=None, max_length=255)


class ContextDigestCreator(ContextModel):
    route_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    prompt_revision: str = Field(min_length=1, max_length=128)


class SessionContextDigest(ContextModel):
    schema_version: int = Field(default=1, ge=1)
    tenant_id: str = Field(min_length=1, max_length=128)
    owner_user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    digest_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    source: ContextDigestSource
    trust_high_watermark: ContextTrust
    facts: tuple[ContextDigestEntry, ...] = Field(default=(), max_length=100)
    decisions: tuple[ContextDigestEntry, ...] = Field(default=(), max_length=100)
    open_tasks: tuple[ContextDigestEntry, ...] = Field(default=(), max_length=100)
    artifact_refs: tuple[ContextDigestObjectRef, ...] = Field(default=(), max_length=100)
    workspace_refs: tuple[ContextDigestObjectRef, ...] = Field(default=(), max_length=20)
    created_by: ContextDigestCreator
    created_at: datetime
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    def hash_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"content_hash"})

    def expected_content_hash(self) -> str:
        return context_digest_content_hash(self.hash_payload())

    @model_validator(mode="after")
    def valid_digest(self) -> SessionContextDigest:
        if self.content_hash != self.expected_content_hash():
            raise ValueError("context digest content hash does not match payload")
        risks = {
            item.trust for group in (self.facts, self.decisions, self.open_tasks) for item in group
        }
        precedence = {
            ContextTrust.SAFE: 0,
            ContextTrust.SENSITIVE: 1,
            ContextTrust.UNTRUSTED: 2,
        }
        if (
            risks
            and max(precedence[item] for item in risks) > precedence[self.trust_high_watermark]
        ):
            raise ValueError("context digest trust watermark is lower than a source fact")
        return self


class SessionContextState(ContextModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    owner_user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    trust_high_watermark: ContextTrust = ContextTrust.SAFE
    latest_digest_id: str | None = Field(default=None, max_length=128)
    latest_digest_version: int = Field(default=0, ge=0)
    transcript_checkpoint_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    updated_at: datetime

    @model_validator(mode="after")
    def digest_pointer_is_complete(self) -> SessionContextState:
        has_id = self.latest_digest_id is not None
        has_version = self.latest_digest_version > 0
        has_checkpoint = self.transcript_checkpoint_hash is not None
        values = (has_id, has_version, has_checkpoint)
        if any(values) and not all(values):
            raise ValueError("context state digest pointer must be complete or empty")
        return self


class SessionContextOverview(ContextModel):
    """Owner-visible Session context state and immutable recovery points."""

    session_id: str = Field(min_length=1, max_length=128)
    state: SessionContextState | None = None
    digests: tuple[SessionContextDigest, ...] = Field(default=(), max_length=50)
    next_before_version: int | None = Field(default=None, ge=1)
    window: ContextWindowSnapshot | None = None
    window_status: ContextWindowAvailability = Field(
        default_factory=lambda: ContextWindowAvailability(status="pending")
    )
