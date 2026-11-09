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


class DesktopState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    STOPPED = "stopped"
    FAILED = "failed"


class ComputerBusyError(ConflictError):
    """Another live holder owns this computer."""


class ComputerRecoveryRequiredError(ConflictError):
    """Old execution must be fenced at the provider before reuse."""


class ComputerLeaseLostError(ConflictError):
    """The caller no longer has permission to act on this computer."""


class ComputerStartRejectedError(RuntimeError):
    """Provider proved that creation was rejected before an instance existed."""


class AssistantComputer(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    computer_id: str
    tenant_id: str
    user_id: str
    provider: str
    image: str
    retired_images: tuple[str, ...] = ()
    volume_ref: str
    volume_ready: bool
    provider_ref: str | None
    state: ComputerState
    epoch: int
    revision: int
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime | None


class ComputerDesktop(BaseModel):
    """One Bot desktop inside an owner's VM.

    ``epoch`` fences execution for this desktop alone. Keyboard and pointer
    ownership has a separate generation in the desktop control lease.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    tenant_id: str
    user_id: str
    specialist_id: str
    desktop_id: str
    computer_id: str
    state: DesktopState
    display: str | None
    unix_user: str | None
    vnc_socket: str | None
    profile_ref: str | None
    workspace_ref: str | None
    image: str | None
    epoch: int
    revision: int
    last_active_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ComputerLease(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    lease_id: str
    computer_id: str
    tenant_id: str
    user_id: str
    owner: str
    purpose: LeasePurpose
    desktop_id: str = "vm"
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
