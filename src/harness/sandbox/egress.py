"""Bind one Run's egress requirement to the sandbox backend that must apply it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from urllib.parse import urlsplit

from harness.core.models import Run
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxEgress,
    SandboxFilePlaneProvider,
    SandboxHandle,
    SandboxProvider,
)

# Builtins that reach the public internet on their own; an Agent holding one
# cannot run behind a host allow list.
_OPEN_INTERNET_BUILTINS = frozenset({"WebSearch", "WebFetch"})

_ENFORCEABLE = "provision_with_egress"


class EgressUnsupportedError(RuntimeError):
    """Raised when a Run's egress requirement cannot be enforced by its backend."""


class EgressScopedSandbox:
    """Apply one Run's egress policy through a backend's policy-aware provision.

    Scoping is per Run rather than bound onto the shared backend: two Runs with
    different requirements execute concurrently, and a provider instance is
    shared between them.
    """

    def __init__(self, backend: SandboxProvider, egress: SandboxEgress) -> None:
        if getattr(backend, _ENFORCEABLE, None) is None:
            raise EgressUnsupportedError(
                f"{type(backend).__name__} cannot enforce a sandbox egress policy"
            )
        self._backend = backend
        self._egress = egress

    async def provision(self, run: Run) -> SandboxHandle:
        provision = getattr(self._backend, _ENFORCEABLE)
        return await provision(run, self._egress)

    async def prepare(self, handle: SandboxHandle) -> None:
        await self._backend.prepare(handle)

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        return await self._backend.execute(
            handle, argv, environment=environment, timeout_seconds=timeout_seconds
        )

    async def collect(self, handle: SandboxHandle) -> None:
        await self._backend.collect(handle)

    async def upload_files(
        self, handle: SandboxHandle, entries: Sequence[tuple[str, bytes]]
    ) -> None:
        """Forward the file plane the wrapper would otherwise mask.

        A scoped wrapper stands in for the backend, so a capability it does not
        forward becomes invisible to the caller that has to detect it.
        """

        await cast(SandboxFilePlaneProvider, self._backend).upload_files(handle, entries)

    async def download_file(self, handle: SandboxHandle, path: str, *, max_bytes: int) -> bytes:
        return await cast(SandboxFilePlaneProvider, self._backend).download_file(
            handle, path, max_bytes=max_bytes
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        await self._backend.destroy(handle)


def _host_of(endpoint_url: str | None) -> str | None:
    if not endpoint_url:
        return None
    parsed = urlsplit(endpoint_url)
    return parsed.hostname or None


def sandbox_egress_policy(
    manifests: Sequence[object],
    capabilities: Mapping[str, object],
    *,
    extra_hosts: Sequence[str] = (),
) -> SandboxEgress | None:
    """Derive a Run's egress requirement from what its Agent may reach.

    Returns ``None`` when the Agent needs the open internet — a builtin web tool
    or an external MCP — because no host list can express that. Otherwise the
    sandbox may only reach the hosts of the MCPs the Agent actually holds, which
    for an Agent with none means no egress at all.

    A referenced MCP that the catalog cannot resolve fails closed: its host
    cannot be allowed, so the Run is refused instead of silently losing access.
    """

    allow: set[str] = set()
    for manifest in manifests:
        spec = getattr(manifest, "spec", None)
        for tool in getattr(spec, "tools", ()) or ():
            if getattr(tool, "builtin", None) in _OPEN_INTERNET_BUILTINS:
                return None
            reference = getattr(tool, "mcp", None)
            if reference is None:
                continue
            capability = capabilities.get(reference)
            if capability is None:
                raise EgressUnsupportedError(
                    f"cannot derive egress for unresolved MCP reference: {reference}"
                )
            if str(getattr(capability, "network_access", "")).endswith("external"):
                return None
            host = _host_of(getattr(capability, "endpoint_url", None))
            if host:
                allow.add(host)
    allow.update(host for host in extra_hosts if host)
    return SandboxEgress(allow_hosts=tuple(sorted(allow)), deny_internet=True)
