"""Sandbox lifecycle contract."""

import io
import os
import tarfile
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
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


class WorkspaceArchiveUnavailableError(RuntimeError):
    """The sandbox could not hand back a workspace archive.

    Raised when the archive route itself is unusable inside the sandbox (no
    ``tar``, or the command is refused), which is a property of the image rather
    than of the workspace. It is deliberately separate from the size and safety
    errors: those must fail the collection, while this one lets a provider take
    the slower per-file route instead of failing a Run that already succeeded.
    """


def workspace_relative_target(root: str, relative: str) -> str:
    """Resolve one workspace-relative path under a remote workspace root.

    The tool gate authorizes workspace-relative names, so a remote file primitive
    has to resolve exactly those and nothing else: an absolute path or an upward
    segment would let a model-approved name land outside the Run workspace.
    """

    candidate = PurePosixPath(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("sandbox file path must stay inside the workspace")
    parts = tuple(part for part in candidate.parts if part not in {"", "."})
    if not parts:
        raise ValueError("sandbox file path must name a file")
    return f"{root.rstrip('/')}/{'/'.join(parts)}"


def workspace_listed_relative(root: str, remote_path: str, *, label: str) -> PurePosixPath:
    """Check one platform-reported path is inside the workspace, and return it relative.

    A listing only feeds the member and size guards, but a path outside the
    workspace means the platform is reporting something this Run does not own, so
    the answer is to refuse rather than to reason about it.
    """

    candidate = PurePosixPath(remote_path)
    relative = (
        candidate.relative_to(PurePosixPath(root))
        if candidate.is_relative_to(PurePosixPath(root))
        else None
    )
    if relative is None or ".." in relative.parts:
        raise ValueError(f"{label} workspace path escaped local collection root")
    return relative


class SandboxFilePlaneProvider(Protocol):
    """A backend that moves bytes without routing them through the command plane.

    Optional on purpose: a backend whose transport only speaks commands simply
    does not implement this, and its callers keep using the command proxy rather
    than failing.
    """

    async def upload_files(
        self, handle: SandboxHandle, entries: Sequence[tuple[str, bytes]]
    ) -> None: ...

    async def download_file(
        self, handle: SandboxHandle, path: str, *, max_bytes: int
    ) -> bytes: ...


def workspace_archive_transfer_limit(*, max_bytes: int, max_members: int) -> int:
    """Wire limit for one collected workspace archive.

    The declared collection bounds count file content; an archive also carries a
    header and padding per member, so a workspace that is exactly at the limit
    still arrives as a slightly larger byte string.
    """

    return max_bytes + max_members * 1024 + 10_240


def extract_workspace_archive(
    content: bytes,
    root: Path,
    *,
    max_bytes: int,
    max_members: int,
    label: str,
) -> None:
    """Unpack one collected workspace archive into the local control-plane mirror.

    The archive was produced inside a remote sandbox, so every member is
    untrusted: a member that is absolute, walks upwards, or is neither a
    directory nor a regular file is rejected rather than resolved. The rule is
    the same for every backend, and it is what keeps a workspace archive from
    writing outside the Run workspace, so it lives here instead of once per
    provider.
    """

    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
    except tarfile.TarError:
        raise ValueError(f"invalid {label} workspace archive") from None
    total = 0
    with archive:
        members = archive.getmembers()
        if len(members) > max_members:
            raise ValueError(f"{label} workspace exceeds collection member limit")
        for member in members:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe {label} workspace archive member")
            parts = tuple(part for part in relative.parts if part not in {"", "."})
            if not parts:
                continue
            target = root.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"unsafe {label} workspace archive member")
            total += member.size
            if total > max_bytes:
                raise ValueError(f"{label} workspace exceeds collection size limit")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"invalid {label} workspace archive")
            data = source.read(max_bytes + 1)
            if len(data) != member.size:
                raise ValueError(f"invalid {label} workspace archive")
            if target.is_symlink():
                raise ValueError(f"unsafe {label} workspace archive member")
            if target.exists() and not target.is_file():
                raise ValueError(f"unsafe {label} workspace archive member")
            # A remote archive may report mode 000. Keep the local control-plane
            # mirror owner-readable so snapshotting cannot fail after a
            # successful model response.
            replace_collected_file(target, data, mode=(member.mode & 0o755) | 0o400)


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
