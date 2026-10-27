"""Durable computer identity and exclusive execution ownership.

Lease expiry is loss of permission, not evidence that the old process stopped.
An expired holder therefore requires recovery before another Run may execute.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from harness.core.errors import ConflictError


class ComputerState(StrEnum):
    REGISTERED = "registered"
    STARTING = "starting"
    IDLE = "idle"
    RUNNING = "running"
    RECOVERING = "recovering"
    FAILED = "failed"


class LeasePurpose(StrEnum):
    START = "start"
    EXECUTE = "execute"
    RECOVER = "recover"


class LeaseState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class ComputerBusyError(ConflictError):
    """Another live holder owns this computer."""


class ComputerRecoveryRequiredError(ConflictError):
    """Old execution must be fenced at the provider before reuse."""


class ComputerLeaseLostError(ConflictError):
    """The caller no longer has permission to act on this computer."""


class AssistantComputer(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    computer_id: str
    tenant_id: str
    user_id: str
    provider: str
    image: str
    volume_ref: str
    provider_ref: str | None
    state: ComputerState
    epoch: int
    revision: int
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime | None


class ComputerLease(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    lease_id: str
    computer_id: str
    tenant_id: str
    user_id: str
    owner: str
    purpose: LeasePurpose
    epoch: int
    run_id: str | None
    session_id: str | None
    state: LeaseState
    created_at: datetime
    renewed_at: datetime
    expires_at: datetime
    finished_at: datetime | None

    @property
    def process_group(self) -> str:
        """Stable provider-side execution namespace; never a host PID."""

        return self.lease_id
