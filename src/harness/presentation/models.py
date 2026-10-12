"""Durable UI envelopes. Presentation content is immutable within a revision."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from harness.presentation.schema import Presentation


class Publication(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: str
    owner_user_id: str
    session_id: str
    agent_name: str
    agent_version: str
    run_id: str
    fencing_token: int
    created_at: datetime
    presentations: tuple[Presentation, ...]


class ActionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str
    tenant_id: str
    actor_user_id: str
    authentication_method: str = "service"
    presentation_id: str
    action_id: str
    revision: int
    idempotency_key: str
    payload_hash: str
    values: dict[str, list[str]]
    status: Literal[
        "accepted",
        "dispatching",
        "submitted",
        "succeeded",
        "failed",
        "cancelled",
        "rejected",
        "timed_out",
    ] = "accepted"
    run_id: str | None = None
    lease_until: datetime | None = None
    fencing_token: int = 0
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None


class ViewState(BaseModel):
    version: int = 0
    state: dict[str, Any] = Field(default_factory=dict)
