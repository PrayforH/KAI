"""Durable sandbox leases: who owns a remote sandbox, until when, and under which epoch.

The Redis session gate serializes Runs inside one Session across Worker replicas,
but it is a mutual-exclusion tool, not an ownership record: it cannot say which
sandbox belongs to which Run, and it disappears when the process dies. A lease
is the durable half — one row per provisioned sandbox, with an owner, a fencing
epoch and an expiry — so an operator can audit live sandboxes and a reaper can
reclaim the ones whose owner never came back.

The epoch is what makes recovery safe: every acquisition for a Session raises it,
so a Worker that resumes after its lease expired can still detect that it no
longer owns the sandbox before touching it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LEASE_TTL_SECONDS = 3600


class SandboxLeaseState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    RECLAIMED = "reclaimed"


class SandboxLease(BaseModel):
    """One owned sandbox. ``epoch`` increments per acquisition of a Session."""

    model_config = ConfigDict(frozen=True)

    lease_id: str
    tenant_id: str
    session_id: str
    run_id: str
    owner: str
    epoch: int = Field(ge=1)
    provider: str
    sandbox_id: str
    state: SandboxLeaseState = SandboxLeaseState.ACTIVE
    created_at: datetime
    expires_at: datetime
    renewed_at: datetime
    released_at: datetime | None = None
    reclaimed_at: datetime | None = None

    def is_live(self, moment: datetime) -> bool:
        return self.state is SandboxLeaseState.ACTIVE and self.expires_at > moment


class SandboxLeaseRepository(Protocol):
    async def add(self, lease: SandboxLease) -> None: ...

    async def get(self, tenant_id: str, lease_id: str) -> SandboxLease: ...

    async def latest_for_session(
        self, tenant_id: str, session_id: str
    ) -> SandboxLease | None: ...

    async def for_run(self, tenant_id: str, run_id: str) -> SandboxLease | None: ...

    async def replace(self, lease: SandboxLease, *, expected_epoch: int) -> bool: ...

    async def list_live(self, tenant_id: str | None = None) -> Sequence[SandboxLease]: ...

    async def list_expired(self, moment: datetime) -> Sequence[SandboxLease]: ...


class SandboxLeaseError(RuntimeError):
    """A lease could not be acquired, renewed or released as asked."""


class SandboxLeaseService:
    """Acquire, renew, release and reap sandbox ownership records."""

    def __init__(
        self,
        repository: SandboxLeaseRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
        default_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        if default_ttl_seconds <= 0:
            raise ValueError("sandbox lease TTL must be positive")
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = id_generator or _lease_ids()
        self._default_ttl_seconds = default_ttl_seconds
        self._lock = asyncio.Lock()

    @property
    def default_ttl_seconds(self) -> int:
        return self._default_ttl_seconds

    async def acquire(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        owner: str,
        provider: str,
        sandbox_id: str,
        ttl_seconds: int | None = None,
    ) -> SandboxLease:
        """Record ownership of a sandbox for one Run.

        A Session may hold only one live lease: a second acquisition while the
        previous one is unexpired means two Workers believe they own the same
        Session, which the session gate is supposed to prevent. Expired leases
        are superseded, and the epoch rises either way.
        """

        if not all(value.strip() for value in (tenant_id, session_id, run_id, owner)):
            raise ValueError("sandbox lease identity must not be empty")
        if not sandbox_id.strip():
            raise ValueError("sandbox lease requires a sandbox id")
        ttl = ttl_seconds or self._default_ttl_seconds
        if ttl <= 0:
            raise ValueError("sandbox lease TTL must be positive")
        async with self._lock:
            now = self._clock()
            previous = await self._repository.latest_for_session(tenant_id, session_id)
            if previous is not None and previous.is_live(now):
                raise SandboxLeaseError(
                    "session already holds a live sandbox lease "
                    f"(owner={previous.owner}, run={previous.run_id})"
                )
            lease = SandboxLease(
                lease_id=self._ids("sandbox_lease"),
                tenant_id=tenant_id,
                session_id=session_id,
                run_id=run_id,
                owner=owner,
                epoch=1 if previous is None else previous.epoch + 1,
                provider=provider,
                sandbox_id=sandbox_id,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl),
                renewed_at=now,
            )
            await self._repository.add(lease)
            return lease

    async def renew(
        self,
        tenant_id: str,
        lease_id: str,
        *,
        epoch: int | None = None,
        ttl_seconds: int | None = None,
    ) -> SandboxLease:
        """Extend a lease that is still owned by the caller's epoch."""

        ttl = ttl_seconds or self._default_ttl_seconds
        async with self._lock:
            lease = await self._repository.get(tenant_id, lease_id)
            if lease.state is not SandboxLeaseState.ACTIVE:
                raise SandboxLeaseError(f"sandbox lease is {lease.state.value}")
            if epoch is not None and epoch != lease.epoch:
                raise SandboxLeaseError("sandbox lease epoch no longer matches")
            now = self._clock()
            updated = lease.model_copy(
                update={"expires_at": now + timedelta(seconds=ttl), "renewed_at": now}
            )
            if not await self._repository.replace(updated, expected_epoch=lease.epoch):
                raise SandboxLeaseError("sandbox lease changed while renewing")
            return updated

    async def release(self, tenant_id: str, lease_id: str, *, epoch: int | None = None) -> None:
        """Mark a lease finished. Releasing a superseded lease is a no-op."""

        async with self._lock:
            lease = await self._repository.get(tenant_id, lease_id)
            if lease.state is not SandboxLeaseState.ACTIVE:
                return
            if epoch is not None and epoch != lease.epoch:
                raise SandboxLeaseError("sandbox lease epoch no longer matches")
            now = self._clock()
            updated = lease.model_copy(
                update={"state": SandboxLeaseState.RELEASED, "released_at": now}
            )
            await self._repository.replace(updated, expected_epoch=lease.epoch)

    async def expired(self, moment: datetime | None = None) -> Sequence[SandboxLease]:
        """Active leases past their expiry — the reaper's input."""

        return await self._repository.list_expired(moment or self._clock())

    async def mark_reclaimed(self, tenant_id: str, lease_id: str) -> SandboxLease:
        async with self._lock:
            lease = await self._repository.get(tenant_id, lease_id)
            now = self._clock()
            updated = lease.model_copy(
                update={"state": SandboxLeaseState.RECLAIMED, "reclaimed_at": now}
            )
            if not await self._repository.replace(updated, expected_epoch=lease.epoch):
                raise SandboxLeaseError("sandbox lease changed while reclaiming")
            return updated

    async def live(self, tenant_id: str | None = None) -> Sequence[SandboxLease]:
        """Ownership inventory for operators and the instance-governance report."""

        return await self._repository.list_live(tenant_id)

    async def for_run(self, tenant_id: str, run_id: str) -> SandboxLease | None:
        return await self._repository.for_run(tenant_id, run_id)


def _lease_ids() -> Callable[[str], str]:
    def generate(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    return generate
