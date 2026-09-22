"""Sandbox lifecycle contract."""

import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

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
    # Execution profiles name the hardened tier "gvisor"; the runtime reaches it
    # through the Kubernetes provider, so both ids map to the same tier.
    "gvisor": SandboxEnforcement.FULL,
    "daytona": SandboxEnforcement.DELEGATED,
    "e2b": SandboxEnforcement.DELEGATED,
    "cubesandbox": SandboxEnforcement.DELEGATED,
    # OpenSandbox on the gVisor runtime delegates the kernel boundary to the
    # provider; the platform only picks the image and the egress policy.
    "opensandbox": SandboxEnforcement.DELEGATED,
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


_SANDBOX_ENFORCEMENT_RANK: dict[SandboxEnforcement, int] = {
    SandboxEnforcement.NONE: 0,
    SandboxEnforcement.DELEGATED: 1,
    SandboxEnforcement.FULL: 2,
}


def sandbox_enforcement_rank(value: SandboxEnforcement) -> int:
    """Order the enforcement tiers so a declared floor can reject weaker ones."""

    return _SANDBOX_ENFORCEMENT_RANK[value]


def provider_meets_enforcement_floor(provider: str, minimum: SandboxEnforcement) -> bool:
    """Whether a concrete backend satisfies an execution profile's floor.

    A profile declares the weakest enforcement it accepts; the deployment has to
    match it with a backend that actually reaches that tier. Unmapped providers
    derive ``none``, so an unknown backend can never satisfy a floor above it.
    """

    return sandbox_enforcement_rank(
        sandbox_enforcement(provider, SandboxIsolation.CONTAINER)
    ) >= sandbox_enforcement_rank(minimum)


_TRUST_ENFORCEMENT_FLOOR: dict[str, SandboxEnforcement] = {
    "safe": SandboxEnforcement.NONE,
    "sensitive": SandboxEnforcement.DELEGATED,
    "untrusted": SandboxEnforcement.FULL,
}


def trust_enforcement_floor(trust: str) -> SandboxEnforcement:
    """The weakest enforcement a Session's trust high-watermark still allows.

    ``ContextTrust`` only ever rises within a Session, so one Run that ingested
    untrusted content raises the floor for every later Run of that Session and
    isolation can never drop back. An unrecognized level fails closed to
    ``full`` rather than quietly permitting a weaker backend.
    """

    return _TRUST_ENFORCEMENT_FLOOR.get(str(trust), SandboxEnforcement.FULL)


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


class SandboxEgress(BaseModel):
    """One Run's egress requirement, as the control plane declared it.

    The platform derives it from what the Agent is allowed to reach; a backend
    either enforces it or the Run is refused, because a declared egress rule
    that nothing applies is not a weaker guarantee but a false one.
    """

    model_config = ConfigDict(frozen=True)

    allow_hosts: tuple[str, ...] = ()
    deny_internet: bool = False

    def is_restrictive(self) -> bool:
        """Report whether this asks for less than unrestricted internet access."""

        return self.deny_internet or bool(self.allow_hosts)


class SandboxResourceUsage(BaseModel):
    """One resource sample of a sandbox, when the platform can report it.

    Platforms differ here: some expose per-sandbox usage, others only the
    allocation agreed at creation. A backend that cannot sample reports nothing
    rather than zeroes, so a missing number is never read as an idle sandbox.
    """

    model_config = ConfigDict(frozen=True)

    cpu_used_pct: float
    mem_used_bytes: int
    mem_total_bytes: int
    disk_used_bytes: int
    disk_total_bytes: int
    sampled_at: datetime


class SandboxCommandResult(BaseModel):
    """Bounded command result without command arguments or environment secrets."""

    model_config = ConfigDict(frozen=True)

    exit_code: int
    stdout: str = ""
    stderr: str = ""


def replace_collected_file(target: Path, content: bytes, *, mode: int | None = None) -> None:
    """Mirror one collected remote file onto its local workspace copy.

    An input artifact is staged read-only, so collection cannot write through the
    local file: the Worker user owns it and is still denied by the absent write
    bit. Publishing a temporary sibling and renaming it over the target needs
    write permission on the directory alone, and leaves the read-only shape the
    input staging step depends on intact.

    ``mode`` is the mode the remote side reported, when it reports one; otherwise
    the target keeps the mode it already had.
    """

    target.parent.mkdir(parents=True, exist_ok=True)
    preserved = mode
    if preserved is None:
        try:
            preserved = target.stat().st_mode & 0o777
        except FileNotFoundError:
            preserved = None
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        if preserved is not None:
            temporary.chmod(preserved)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


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
