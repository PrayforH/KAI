"""PostgreSQL authority for owner computers and their exclusive holders.

All writes lock the owner row, including lease renewal and recovery. Database
time is sampled *after* the lock so queueing and worker clock skew cannot grant
an expired holder a fresh lease. Provider I/O never holds this transaction open.

This is an internal control-plane service, not an authorization API. Callers
must resolve an authenticated owner first; no method accepts a user-controlled
provider endpoint, volume path or computer id as an access grant.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from harness.computer.models import (
    AssistantComputer,
    ComputerBusyError,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerRecoveryRequiredError,
    ComputerState,
    LeasePurpose,
    LeaseState,
)
from harness.computer.storage import AssistantComputerRow as Computer
from harness.computer.storage import ComputerLeaseRow as Lease
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory


class ComputerRegistry:
    def __init__(self, sessions: SessionFactory, *, lease_ttl_seconds: int = 120) -> None:
        if not 10 <= lease_ttl_seconds <= 3600:
            raise ValueError("computer lease TTL must be between 10 and 3600 seconds")
        self._sessions = sessions
        self._ttl = timedelta(seconds=lease_ttl_seconds)

    @property
    def lease_ttl_seconds(self) -> float:
        return self._ttl.total_seconds()

    async def ensure(
        self, tenant: str, user: str, *, provider: str, image: str
    ) -> AssistantComputer:
        """Reserve one identity and volume name before creating external resources.

        Concurrent callers receive the same saved identity. Configuration never
        silently changes an existing computer or detaches its work disk.
        """

        _identity(tenant, 128)
        _identity(user, 128)
        _identity(provider, 64)
        _identity(image, 512)
        identifier = uuid4().hex
        async with self._sessions.begin() as session:
            moment = await _now(session)
            await session.execute(
                insert(Computer)
                .values(
                    tenant_id=tenant,
                    user_id=user,
                    computer_id="computer_" + identifier,
                    provider=provider,
                    image=image,
                    volume_ref="kai-computer-" + identifier,
                    volume_ready=False,
                    state=ComputerState.REGISTERED.value,
                    epoch=0,
                    revision=1,
                    created_at=moment,
                    updated_at=moment,
                )
                .on_conflict_do_nothing(index_elements=[Computer.tenant_id, Computer.user_id])
            )
            row = await self._get(session, tenant, user)
            if row.provider != provider or row.image != image:
                raise ConflictError("computer configuration requires an explicit migration")
            return AssistantComputer.model_validate(row)

    async def get(self, tenant: str, user: str) -> AssistantComputer:
        async with self._sessions() as session:
            return AssistantComputer.model_validate(await self._get(session, tenant, user))

    async def claim_start(self, tenant: str, user: str, *, owner: str) -> ComputerLease:
        return await self._claim(tenant, user, owner=owner, purpose=LeasePurpose.START)

    async def claim_execution(
        self,
        tenant: str,
        user: str,
        *,
        owner: str,
        run_id: str,
        session_id: str,
        run_fencing_token: int | None = None,
    ) -> ComputerLease:
        _identity(run_id, 128)
        _identity(session_id, 128)
        if run_fencing_token is not None and (
            type(run_fencing_token) is not int or run_fencing_token < 0
        ):
            raise ValueError("computer execution Run fence is invalid")
        return await self._claim(
            tenant,
            user,
            owner=owner,
            purpose=LeasePurpose.EXECUTE,
            run_fencing_token=run_fencing_token,
            run_id=run_id,
            session_id=session_id,
        )

    async def claim_recovery(self, tenant: str, user: str, *, owner: str) -> ComputerLease:
        """Fence the old database holder, but do not authorize new execution.

        The provider must quiesce old processes (or prove the old instance gone)
        before complete_recovery. Expiry alone never makes a computer reusable.
        """

        return await self._claim(tenant, user, owner=owner, purpose=LeasePurpose.RECOVER)

    async def _claim(
        self,
        tenant: str,
        user: str,
        *,
        owner: str,
        purpose: LeasePurpose,
        run_id: str | None = None,
        session_id: str | None = None,
        run_fencing_token: int | None = None,
    ) -> ComputerLease:
        _identity(owner, 160)
        async with self._sessions.begin() as session:
            computer = await self._get(session, tenant, user, lock=True)
            moment = await _now(session)
            previous = await session.scalar(
                select(Lease).where(
                    Lease.tenant_id == tenant,
                    Lease.user_id == user,
                    Lease.epoch == computer.epoch,
                )
            )
            if previous and previous.state == LeaseState.ACTIVE:
                if previous.expires_at > moment:
                    raise ComputerBusyError("computer already has a live holder")
                if purpose is not LeasePurpose.RECOVER:
                    raise ComputerRecoveryRequiredError(
                        "expired execution requires provider fencing"
                    )
                previous.state, previous.finished_at = LeaseState.EXPIRED.value, moment
                # Retire the old row before inserting its replacement: the
                # database also enforces one active holder per owner.
                await session.flush()
            if purpose is LeasePurpose.START:
                if computer.provider_ref is not None:
                    raise ConflictError("computer is already attached; reconnect or recover it")
                if computer.state not in {ComputerState.REGISTERED, ComputerState.FAILED}:
                    raise ComputerRecoveryRequiredError("unfinished provisioning requires recovery")
                state = ComputerState.STARTING
            elif purpose is LeasePurpose.EXECUTE:
                if computer.state != ComputerState.IDLE or computer.provider_ref is None:
                    raise ComputerRecoveryRequiredError("computer is not ready for execution")
                state = ComputerState.RUNNING
            else:
                state = ComputerState.RECOVERING
            computer.epoch += 1
            _touch(computer, moment, state)
            lease = Lease(
                lease_id="computer_lease_" + uuid4().hex,
                tenant_id=tenant,
                user_id=user,
                computer_id=computer.computer_id,
                owner=owner,
                purpose=purpose.value,
                epoch=computer.epoch,
                run_id=run_id,
                run_fencing_token=run_fencing_token,
                session_id=session_id,
                state=LeaseState.ACTIVE.value,
                created_at=moment,
                renewed_at=moment,
                expires_at=moment + self._ttl,
            )
            session.add(lease)
            await session.flush()
            return ComputerLease.model_validate(lease)

    async def renew(self, token: ComputerLease) -> ComputerLease:
        async with self._holder(token) as (session, computer, lease, moment):
            lease.renewed_at, lease.expires_at = moment, moment + self._ttl
            _touch(computer, moment)
            await session.flush()
            return ComputerLease.model_validate(lease)

    async def holder(self, token: ComputerLease) -> tuple[AssistantComputer, ComputerLease]:
        """Validate without extending permission; used by the privileged control service."""

        async with self._sessions() as session:
            return await self._view(session, token)

    @asynccontextmanager
    async def host_operation(
        self, token: ComputerLease
    ) -> AsyncGenerator[tuple[AssistantComputer, ComputerLease]]:
        """Serialize physical lifecycle without blocking ownership heartbeats.

        Validate *after* acquiring the advisory lock. All host lifecycle calls
        use this gate; root command calls instead use the physical epoch gate.
        No computer row lock is held across a slow Docker request.
        """

        async with self._sessions.begin() as session:
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended("kai-computer-host:" + token.computer_id, 0)
                    )
                )
            )
            yield await self._view(session, token)

    @staticmethod
    async def _view(
        session: AsyncSession, token: ComputerLease
    ) -> tuple[AssistantComputer, ComputerLease]:
        pair = (
            await session.execute(
                select(Computer, Lease)
                .join(
                    Lease,
                    (
                        (Lease.tenant_id == Computer.tenant_id)
                        & (Lease.user_id == Computer.user_id)
                        & (Lease.epoch == Computer.epoch)
                    ),
                )
                .where(
                    Computer.tenant_id == token.tenant_id,
                    Computer.user_id == token.user_id,
                    Computer.computer_id == token.computer_id,
                    Lease.lease_id == token.lease_id,
                    Lease.epoch == token.epoch,
                    Lease.state == LeaseState.ACTIVE.value,
                    Lease.expires_at > func.clock_timestamp(),
                )
            )
        ).one_or_none()
        if pair is None:
            raise ComputerLeaseLostError("computer lease is expired, released or superseded")
        computer, lease = pair
        if (
            lease.owner != token.owner
            or lease.purpose != token.purpose
            or lease.run_id != token.run_id
            or lease.session_id != token.session_id
        ):
            raise ComputerLeaseLostError("computer lease identity does not match")
        return AssistantComputer.model_validate(computer), ComputerLease.model_validate(lease)

    async def complete_start(self, token: ComputerLease, *, provider_ref: str) -> AssistantComputer:
        """Publish a ready instance using the previously reserved work volume.

        Its provider metadata must contain computer_id and this lease's epoch,
        so a crash before this commit is discoverable by recovery.
        """

        _identity(provider_ref, 128)
        async with self._holder(token, LeasePurpose.START) as (_, computer, lease, moment):
            if computer.provider_ref is not None and computer.provider_ref != provider_ref:
                raise ConflictError("computer already has a provider instance")
            computer.provider_ref = provider_ref
            computer.volume_ready = True
            _finish(computer, lease, moment, ComputerState.IDLE)
            return AssistantComputer.model_validate(computer)

    async def record_start_instance(self, token: ComputerLease, provider_ref: str) -> None:
        """Save the creation acknowledgement before waiting for readiness.

        This closes the ordinary crash window between instance creation and
        initialization. An unknown POST outcome still requires discovery.
        """

        _identity(provider_ref, 128)
        async with self._holder(token, LeasePurpose.START) as (_, computer, _, moment):
            if computer.provider_ref not in {None, provider_ref}:
                raise ConflictError("creation attempt already recorded another instance")
            computer.provider_ref = provider_ref
            _touch(computer, moment)

    async def reject_start(self, token: ComputerLease) -> None:
        """Only a definite pre-creation rejection may release a failed start.

        A timeout, cancellation, malformed acknowledgement or readiness failure
        is uncertain and must retain its lease for discovery/recovery instead.
        """

        async with self._holder(token, LeasePurpose.START) as (_, computer, lease, moment):
            if computer.provider_ref is not None:
                raise ConflictError("an acknowledged instance requires recovery")
            _finish(computer, lease, moment, ComputerState.FAILED)

    async def finish_execution(self, token: ComputerLease) -> AssistantComputer:
        """Release after the driver confirmed this lease's process group exited.

        Do not call this in an unconditional finally block: a failed cleanup
        must retain ownership until recovery can fence the old process.
        """

        async with self._holder(token, LeasePurpose.EXECUTE) as (_, computer, lease, moment):
            computer.last_active_at = moment
            _finish(computer, lease, moment, ComputerState.IDLE)
            return AssistantComputer.model_validate(computer)

    async def complete_recovery(
        self, token: ComputerLease, *, provider_ref: str | None
    ) -> AssistantComputer:
        """Commit provider fencing; null means confirmed absent, never 'unknown'.

        The work volume identity is preserved even when an instance is lost.
        A provider failure must leave recovery unfinished, not call this method.
        """

        if provider_ref is not None:
            _identity(provider_ref, 128)
        async with self._holder(token, LeasePurpose.RECOVER) as (_, computer, lease, moment):
            computer.provider_ref = provider_ref
            if provider_ref is not None:
                computer.volume_ready = True
            _finish(
                computer,
                lease,
                moment,
                ComputerState.IDLE if provider_ref is not None else ComputerState.REGISTERED,
            )
            return AssistantComputer.model_validate(computer)

    @asynccontextmanager
    async def _holder(
        self, token: ComputerLease, purpose: LeasePurpose | None = None
    ) -> AsyncGenerator[tuple[AsyncSession, Computer, Lease, datetime]]:
        async with self._sessions.begin() as session:
            computer = await self._get(session, token.tenant_id, token.user_id, lock=True)
            moment = await _now(session)
            lease = await session.get(Lease, token.lease_id)
            if (
                lease is None
                or lease.computer_id != computer.computer_id
                or token.computer_id != computer.computer_id
                or lease.tenant_id != token.tenant_id
                or lease.user_id != token.user_id
                or lease.owner != token.owner
                or lease.epoch != token.epoch
                or computer.epoch != token.epoch
                or lease.state != LeaseState.ACTIVE
                or lease.expires_at <= moment
                or lease.purpose != token.purpose
                or lease.run_id != token.run_id
                or lease.session_id != token.session_id
                or (purpose is not None and lease.purpose != purpose)
            ):
                raise ComputerLeaseLostError("computer lease is expired, released or superseded")
            yield session, computer, lease, moment

    @staticmethod
    async def _get(
        session: AsyncSession, tenant: str, user: str, *, lock: bool = False
    ) -> Computer:
        query = select(Computer).where(Computer.tenant_id == tenant, Computer.user_id == user)
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise NotFoundError("owner computer not found")
        return row


async def _now(session: AsyncSession) -> datetime:
    moment = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(moment, datetime)
    return moment


def _identity(value: str, limit: int) -> None:
    if not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError("computer identity is empty, too long or contains control characters")


def _touch(computer: Computer, moment: datetime, state: ComputerState | None = None) -> None:
    computer.revision += 1
    computer.updated_at = moment
    if state is not None:
        computer.state = state.value


def _finish(computer: Computer, lease: Lease, moment: datetime, state: ComputerState) -> None:
    lease.state, lease.finished_at = LeaseState.RELEASED.value, moment
    _touch(computer, moment, state)
