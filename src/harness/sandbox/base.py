"""Sandbox lifecycle contract."""

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from harness.core.models import Run


class SandboxIsolation(StrEnum):
    WORKSPACE = "workspace"
    CONTAINER = "container"


class SandboxEnforcement(StrEnum):
    """How much of the isolation claim the platform enforces itself.

    ``full``      the platform configures the kernel boundary (gVisor Pod plus
                  default-deny NetworkPolicy) and can verify it held.
    ``delegated`` a provider-enforced container/microVM boundary the platform
                  trusts but does not own (Daytona, E2B, CubeSandbox).
    ``none``      same-world execution; only tool-gate interception applies.
    """

    FULL = "full"
    DELEGATED = "delegated"
    NONE = "none"


_SANDBOX_ENFORCEMENT_BY_PROVIDER: dict[str, SandboxEnforcement] = {
    "kubernetes": SandboxEnforcement.FULL,
    "daytona": SandboxEnforcement.DELEGATED,
    "e2b": SandboxEnforcement.DELEGATED,
    "cubesandbox": SandboxEnforcement.DELEGATED,
}


def sandbox_enforcement(
    provider: str, isolation: SandboxIsolation
) -> SandboxEnforcement:
    """Derive the per-run enforcement fact from provider-generated state only.

    Deferred wrappers report ``<provider>-deferred``; strip the suffix before
    mapping. Unknown container providers fail closed to ``none`` so a missing
    mapping never upgrades reported isolation.
    """

    if isolation is not SandboxIsolation.CONTAINER:
        return SandboxEnforcement.NONE
    base = provider.removesuffix("-deferred")
    return _SANDBOX_ENFORCEMENT_BY_PROVIDER.get(base, SandboxEnforcement.NONE)


class SandboxHandle(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    sandbox_id: str
    path: Path
    provider: str = "local"
    isolation_level: SandboxIsolation = SandboxIsolation.WORKSPACE
    remote_workspace: str | None = None
    preserve_remote_workspace: bool = Field(default=False, exclude=True)
    runtime_transport_factory: Callable[[object], object] | None = Field(
        default=None, exclude=True, repr=False
    )
    deferred_tool_execution: bool = Field(default=False, exclude=True)


class SandboxCommandResult(BaseModel):
    """Bounded command result without command arguments or environment secrets."""

    model_config = ConfigDict(frozen=True)

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class SandboxProvider(Protocol):
    async def provision(self, run: Run) -> SandboxHandle: ...

    async def prepare(self, handle: SandboxHandle) -> None: ...

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult: ...

    async def collect(self, handle: SandboxHandle) -> None: ...

    async def destroy(self, handle: SandboxHandle) -> None: ...
