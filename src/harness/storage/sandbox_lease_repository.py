"""Sandbox lease persistence: one durable owner per provisioned sandbox."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harness.core.errors import ConflictError, NotFoundError
from harness.sandbox.lease import SandboxLease, SandboxLeaseState
from harness.storage.models import SandboxLeaseRow


def _to_domain(row: SandboxLeaseRow) -> SandboxLease:
    return SandboxLease(
        lease_id=row.lease_id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        run_id=row.run_id,
        owner=row.owner,
        epoch=row.epoch,
        provider=row.provider,
        sandbox_id=row.sandbox_id,
        state=SandboxLeaseState(row.state),
        created_at=row.created_at,
        expires_at=row.expires_at,
        renewed_at=row.renewed_at,
        released_at=row.released_at,
        reclaimed_at=row.reclaimed_at,
    )


class PostgresSandboxLeaseRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, lease: SandboxLease) -> None:
        async with self._sessions() as session:
            existing = await session.get(SandboxLeaseRow, (lease.tenant_id, lease.lease_id))
            if existing is not None:
                raise ConflictError("sandbox lease already exists")
            session.add(
                SandboxLeaseRow(
                    tenant_id=lease.tenant_id,
                    lease_id=lease.lease_id,
                    session_id=lease.session_id,
                    run_id=lease.run_id,
                    owner=lease.owner,
                    epoch=lease.epoch,
                    provider=lease.provider,
                    sandbox_id=lease.sandbox_id,
                    state=lease.state.value,
                    created_at=lease.created_at,
                    expires_at=lease.expires_at,
                    renewed_at=lease.renewed_at,
                    released_at=lease.released_at,
                    reclaimed_at=lease.reclaimed_at,
                )
            )
            await session.commit()

    async def get(self, tenant_id: str, lease_id: str) -> SandboxLease:
        async with self._sessions() as session:
            row = await session.get(SandboxLeaseRow, (tenant_id, lease_id))
            if row is None:
                raise NotFoundError("sandbox lease not found")
            return _to_domain(row)

    async def latest_for_session(
        self, tenant_id: str, session_id: str
    ) -> SandboxLease | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(SandboxLeaseRow)
                .where(
                    SandboxLeaseRow.tenant_id == tenant_id,
                    SandboxLeaseRow.session_id == session_id,
                )
                .order_by(SandboxLeaseRow.epoch.desc())
                .limit(1)
            )
            return None if row is None else _to_domain(row)

    async def for_run(self, tenant_id: str, run_id: str) -> SandboxLease | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(SandboxLeaseRow)
                .where(
                    SandboxLeaseRow.tenant_id == tenant_id,
                    SandboxLeaseRow.run_id == run_id,
                )
                .order_by(SandboxLeaseRow.epoch.desc())
                .limit(1)
            )
            return None if row is None else _to_domain(row)

    async def replace(self, lease: SandboxLease, *, expected_epoch: int) -> bool:
        """Compare-and-set on the epoch, so a stale owner cannot rewrite a lease."""

        async with self._sessions() as session:
            row = await session.get(SandboxLeaseRow, (lease.tenant_id, lease.lease_id))
            if row is None:
                raise NotFoundError("sandbox lease not found")
            if row.epoch != expected_epoch:
                return False
            row.epoch = lease.epoch
            row.state = lease.state.value
            row.expires_at = lease.expires_at
            row.renewed_at = lease.renewed_at
            row.released_at = lease.released_at
            row.reclaimed_at = lease.reclaimed_at
            await session.commit()
            return True

    async def list_live(self, tenant_id: str | None = None) -> Sequence[SandboxLease]:
        async with self._sessions() as session:
            statement = select(SandboxLeaseRow).where(
                SandboxLeaseRow.state == SandboxLeaseState.ACTIVE.value
            )
            if tenant_id is not None:
                statement = statement.where(SandboxLeaseRow.tenant_id == tenant_id)
            rows = (await session.scalars(statement.order_by(SandboxLeaseRow.created_at))).all()
            return [_to_domain(row) for row in rows]

    async def list_expired(self, moment: datetime) -> Sequence[SandboxLease]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(SandboxLeaseRow)
                    .where(
                        SandboxLeaseRow.state == SandboxLeaseState.ACTIVE.value,
                        SandboxLeaseRow.expires_at < moment,
                    )
                    .order_by(SandboxLeaseRow.expires_at)
                )
            ).all()
            return [_to_domain(row) for row in rows]


class InMemorySandboxLeaseRepository:
    def __init__(self) -> None:
        self._leases: dict[tuple[str, str], SandboxLease] = {}

    async def add(self, lease: SandboxLease) -> None:
        self._leases[(lease.tenant_id, lease.lease_id)] = lease

    async def get(self, tenant_id: str, lease_id: str) -> SandboxLease:
        try:
            return self._leases[(tenant_id, lease_id)]
        except KeyError as error:
            raise NotFoundError("sandbox lease not found") from error

    async def latest_for_session(
        self, tenant_id: str, session_id: str
    ) -> SandboxLease | None:
        candidates = [
            lease
            for (lease_tenant, _), lease in self._leases.items()
            if lease_tenant == tenant_id and lease.session_id == session_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda lease: lease.epoch)

    async def for_run(self, tenant_id: str, run_id: str) -> SandboxLease | None:
        candidates = [
            lease
            for (lease_tenant, _), lease in self._leases.items()
            if lease_tenant == tenant_id and lease.run_id == run_id
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda lease: lease.epoch)

    async def replace(self, lease: SandboxLease, *, expected_epoch: int) -> bool:
        key = (lease.tenant_id, lease.lease_id)
        current = self._leases.get(key)
        if current is None:
            raise NotFoundError("sandbox lease not found")
        if current.epoch != expected_epoch:
            return False
        self._leases[key] = lease
        return True

    async def list_live(self, tenant_id: str | None = None) -> Sequence[SandboxLease]:
        return sorted(
            (
                lease
                for (lease_tenant, _), lease in self._leases.items()
                if lease.state is SandboxLeaseState.ACTIVE
                and (tenant_id is None or lease_tenant == tenant_id)
            ),
            key=lambda lease: lease.created_at,
        )

    async def list_expired(self, moment: datetime) -> Sequence[SandboxLease]:
        return sorted(
            (
                lease
                for lease in self._leases.values()
                if lease.state is SandboxLeaseState.ACTIVE and lease.expires_at < moment
            ),
            key=lambda lease: lease.expires_at,
        )
