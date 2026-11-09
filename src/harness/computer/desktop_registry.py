"""Allocate and fence one desktop per Bot inside an owner's VM.

Display numbers and UNIX accounts are derived from the durable desktop
identity, so a rebuilt guest recreates the same desktop instead of reshuffling
another Bot's profile. Database rows, not guest state, decide who owns a slot.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.computer.models import ComputerDesktop, DesktopState
from harness.computer.storage import ComputerDesktopRow as Desktop
from harness.computer.storage import ComputerLeaseRow as Lease
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory

FIRST_DISPLAY = 100
LAST_DISPLAY = 199
LIVE_STATES = (DesktopState.STARTING.value, DesktopState.IDLE.value, DesktopState.BUSY.value)


def desktop_id_for(tenant: str, user: str, specialist_id: str) -> str:
    """Stable identity: retrying ensure never creates a second desktop."""

    digest = sha256(f"{tenant}\0{user}\0{specialist_id}".encode()).hexdigest()[:24]
    return f"desktop_{digest}"


def unix_user_for(desktop_id: str) -> str:
    return "kai-bot-" + sha256(desktop_id.encode()).hexdigest()[:8]


def profile_refs(desktop_id: str) -> tuple[str, str]:
    short = sha256(desktop_id.encode()).hexdigest()[:16]
    base = f"/home/kai-bots/{short}"
    return f"{base}/chromium", f"{base}/workspace"


class DesktopRegistry:
    """Canonical per-Bot desktop records; guest processes are managed elsewhere."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def ensure(
        self,
        tenant: str,
        user: str,
        specialist_id: str,
        *,
        computer_id: str,
        image: str | None = None,
    ) -> ComputerDesktop:
        """Reserve a desktop slot for one Bot, idempotent on the Bot identity."""

        _identity(tenant, 128)
        _identity(user, 128)
        _identity(specialist_id, 128)
        _identity(computer_id, 64)
        desktop_id = desktop_id_for(tenant, user, specialist_id)
        profile_ref, workspace_ref = profile_refs(desktop_id)
        for _ in range(len(range(FIRST_DISPLAY, LAST_DISPLAY + 1))):
            try:
                async with self._sessions.begin() as session:
                    existing = await self._get_or_none(session, tenant, user, desktop_id)
                    if existing is not None:
                        if existing.state == DesktopState.STOPPED.value:
                            # A stopped slot revives on its next Run: the
                            # guest unit restarts from the reserved state.
                            existing.state = DesktopState.CREATED.value
                            existing.revision += 1
                            existing.updated_at = await _now(session)
                            await session.flush()
                        return ComputerDesktop.model_validate(existing)
                    moment = await _now(session)
                    # A slot id is deterministic, so a rebuilt slot must never
                    # reuse an epoch its historical leases already consumed.
                    prior = await session.scalar(
                        select(func.coalesce(func.max(Lease.epoch), 0)).where(
                            Lease.tenant_id == tenant,
                            Lease.user_id == user,
                            Lease.desktop_id == desktop_id,
                        )
                    )
                    desktop = Desktop(
                        tenant_id=tenant,
                        user_id=user,
                        specialist_id=specialist_id,
                        desktop_id=desktop_id,
                        computer_id=computer_id,
                        state=DesktopState.CREATED.value,
                        display=await self._free_display(session, tenant, user, desktop_id),
                        unix_user=unix_user_for(desktop_id),
                        vnc_socket=f"/run/kai-bots/{desktop_id}/vnc.sock",
                        profile_ref=profile_ref,
                        workspace_ref=workspace_ref,
                        image=image,
                        epoch=int(prior or 0),
                        revision=1,
                        last_active_at=None,
                        created_at=moment,
                        updated_at=moment,
                    )
                    session.add(desktop)
                    await session.flush()
                    return ComputerDesktop.model_validate(desktop)
            except IntegrityError:
                # Another worker reserved the same Bot or display first; the
                # next attempt observes the committed row or takes the slot on.
                continue
        raise ConflictError("no free desktop display is available")

    async def get(self, tenant: str, user: str, specialist_id: str) -> ComputerDesktop:
        desktop_id = desktop_id_for(tenant, user, specialist_id)
        async with self._sessions() as session:
            row = await self._get_or_none(session, tenant, user, desktop_id)
        if row is None:
            raise NotFoundError("bot desktop not found")
        return ComputerDesktop.model_validate(row)

    async def get_by_id(self, tenant: str, user: str, desktop_id: str) -> ComputerDesktop:
        """Read a slot by its stable id: the Run already knows which one it owns."""

        async with self._sessions() as session:
            row = await self._get_or_none(session, tenant, user, desktop_id)
        if row is None:
            raise NotFoundError("bot desktop not found")
        return ComputerDesktop.model_validate(row)

    async def list(self, tenant: str, user: str) -> tuple[ComputerDesktop, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(Desktop)
                    .where(Desktop.tenant_id == tenant, Desktop.user_id == user)
                    .order_by(Desktop.created_at, Desktop.desktop_id)
                )
            ).all()
        return tuple(ComputerDesktop.model_validate(row) for row in rows)

    async def reapable(self, *, idle_seconds: float) -> tuple[ComputerDesktop, ...]:
        """Slots a reaper may stop: already stopped, or idle past the window."""

        moment = datetime.now(UTC)
        cutoff = moment - timedelta(seconds=max(idle_seconds, 60))
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(Desktop)
                    .where(
                        Desktop.state.in_((DesktopState.STOPPED.value, DesktopState.IDLE.value)),
                        (Desktop.state == DesktopState.STOPPED.value)
                        | (Desktop.last_active_at.is_(None))
                        | (Desktop.last_active_at < cutoff),
                    )
                    .order_by(Desktop.last_active_at.asc().nulls_first())
                )
            ).all()
        return tuple(ComputerDesktop.model_validate(row) for row in rows)

    async def stop(self, tenant: str, user: str, desktop_id: str) -> ComputerDesktop:
        """Fence running work and mark the desktop stopped; files stay on disk."""

        async with self._sessions.begin() as session:
            row = await self._get_or_none(session, tenant, user, desktop_id)
            if row is None:
                raise NotFoundError("bot desktop not found")
            if row.state == DesktopState.STOPPED.value:
                return ComputerDesktop.model_validate(row)
            moment = await _now(session)
            await session.execute(
                update(Lease)
                .where(
                    Lease.tenant_id == tenant,
                    Lease.user_id == user,
                    Lease.desktop_id == desktop_id,
                    Lease.state == "active",
                )
                .values(state="expired", finished_at=moment)
            )
            row.epoch += 1
            row.state = DesktopState.STOPPED.value
            row.revision += 1
            row.updated_at = moment
            await session.flush()
            return ComputerDesktop.model_validate(row)

    async def mark_started(self, tenant: str, user: str, desktop_id: str) -> ComputerDesktop:
        async with self._sessions.begin() as session:
            row = await self._get_or_none(session, tenant, user, desktop_id)
            if row is None:
                raise NotFoundError("bot desktop not found")
            moment = await _now(session)
            row.state = DesktopState.IDLE.value
            row.last_active_at = moment
            row.revision += 1
            row.updated_at = moment
            await session.flush()
            return ComputerDesktop.model_validate(row)

    async def _free_display(
        self, session: AsyncSession, tenant: str, user: str, desktop_id: str
    ) -> str:
        taken = set(
            (
                await session.scalars(
                    select(Desktop.display).where(
                        Desktop.tenant_id == tenant,
                        Desktop.user_id == user,
                        Desktop.state.in_(LIVE_STATES),
                        Desktop.display.is_not(None),
                    )
                )
            ).all()
        )
        span = LAST_DISPLAY - FIRST_DISPLAY + 1
        start = FIRST_DISPLAY + int(sha256(desktop_id.encode()).hexdigest()[:4], 16) % span
        for offset in range(span):
            number = FIRST_DISPLAY + (start - FIRST_DISPLAY + offset) % span
            display = f":{number}"
            if display not in taken:
                return display
        raise ConflictError("no free desktop display is available")

    @staticmethod
    async def _get_or_none(
        session: AsyncSession, tenant: str, user: str, desktop_id: str
    ) -> Desktop | None:
        return await session.scalar(
            select(Desktop).where(
                Desktop.tenant_id == tenant,
                Desktop.user_id == user,
                Desktop.desktop_id == desktop_id,
            )
        )


async def _now(session: AsyncSession) -> datetime:
    moment = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(moment, datetime)
    return moment


def _identity(value: str, limit: int) -> None:
    if not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError("desktop identity is empty, too long or contains control characters")
