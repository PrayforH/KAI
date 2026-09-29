"""Durable Run dispatch: transactional acceptance and lease-based delivery.

The platform's acceptance answer is only honest if the obligation to execute
already exists somewhere durable. These two adapters provide that: one writes
the Run, its ``run.queued`` event and the dispatch command in a single
transaction, the other hands committed commands to Redis under a lease that
survives a crashed Dispatcher.
"""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError
from harness.core.events import RunEvent
from harness.core.models import Run
from harness.core.ports import (
    ExecutionCommandStatus,
    RunExecutionCommand,
    RunExecutionCommandBacklog,
)
from harness.storage.database import SessionFactory
from harness.storage.models import RunExecutionCommandRow
from harness.storage.repositories import build_event_row, build_run_row


def build_command_row(command: RunExecutionCommand) -> RunExecutionCommandRow:
    return RunExecutionCommandRow(
        command_id=command.command_id,
        tenant_id=command.tenant_id,
        run_id=command.run_id,
        session_id=command.session_id,
        status=command.status.value,
        created_at=command.created_at,
        available_at=command.available_at,
        attempts=command.attempts,
        failures=command.failures,
        lease_owner=command.lease_owner,
        lease_expires_at=command.lease_expires_at,
        dispatched_at=command.dispatched_at,
        last_error=command.last_error,
    )


def _to_command(row: RunExecutionCommandRow) -> RunExecutionCommand:
    return RunExecutionCommand(
        command_id=row.command_id,
        tenant_id=row.tenant_id,
        run_id=row.run_id,
        session_id=row.session_id,
        status=ExecutionCommandStatus(row.status),
        created_at=row.created_at,
        available_at=row.available_at,
        attempts=row.attempts,
        failures=row.failures,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        dispatched_at=row.dispatched_at,
        last_error=row.last_error,
    )


def _claimable(now: datetime):
    return (
        RunExecutionCommandRow.status == ExecutionCommandStatus.PENDING.value,
        RunExecutionCommandRow.available_at <= now,
        or_(
            RunExecutionCommandRow.lease_expires_at.is_(None),
            RunExecutionCommandRow.lease_expires_at <= now,
        ),
    )


class PostgresRunExecutionCommandRepository:
    """Lease-based pending-dispatch queue held in PostgreSQL.

    Several Dispatcher replicas can share this table. ``FOR UPDATE SKIP
    LOCKED`` gives each claimant a disjoint batch, and lease expiry is what
    recovers commands whose Dispatcher died between publish and acknowledge.
    """

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def claim_pending(
        self,
        *,
        owner: str,
        lease_seconds: float,
        limit: int,
        now: datetime,
    ) -> list[RunExecutionCommand]:
        statement = (
            select(RunExecutionCommandRow)
            .where(*_claimable(now))
            .order_by(
                RunExecutionCommandRow.available_at,
                RunExecutionCommandRow.command_id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).scalars().all()
            for row in rows:
                row.lease_owner = owner
                row.lease_expires_at = lease_until
                row.attempts = row.attempts + 1
            claimed = [_to_command(row) for row in rows]
            await session.commit()
            return claimed

    async def mark_dispatched(self, command: RunExecutionCommand, *, now: datetime) -> bool:
        if command.lease_owner is None:  # pragma: no cover - claim always sets it
            raise ValueError("cannot mark a command dispatched without a lease owner")
        statement = (
            update(RunExecutionCommandRow)
            .where(
                RunExecutionCommandRow.command_id == command.command_id,
                RunExecutionCommandRow.status == ExecutionCommandStatus.PENDING.value,
                RunExecutionCommandRow.lease_owner == command.lease_owner,
            )
            .values(
                status=ExecutionCommandStatus.DISPATCHED.value,
                dispatched_at=now,
                lease_owner=None,
                lease_expires_at=None,
                last_error=None,
            )
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def reschedule(
        self,
        command: RunExecutionCommand,
        *,
        available_at: datetime,
        error: str | None,
    ) -> bool:
        if command.lease_owner is None:  # pragma: no cover - claim always sets it
            raise ValueError("cannot reschedule a command without a lease owner")
        statement = (
            update(RunExecutionCommandRow)
            .where(
                RunExecutionCommandRow.command_id == command.command_id,
                RunExecutionCommandRow.status == ExecutionCommandStatus.PENDING.value,
                RunExecutionCommandRow.lease_owner == command.lease_owner,
            )
            .values(
                available_at=available_at,
                failures=RunExecutionCommandRow.failures + 1,
                lease_owner=None,
                lease_expires_at=None,
                last_error=error,
            )
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def get(self, tenant_id: str, run_id: str) -> RunExecutionCommand | None:
        statement = select(RunExecutionCommandRow).where(
            RunExecutionCommandRow.tenant_id == tenant_id,
            RunExecutionCommandRow.run_id == run_id,
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return None if row is None else _to_command(row)

    async def backlog(self, *, now: datetime) -> RunExecutionCommandBacklog:
        pending = RunExecutionCommandRow.status == ExecutionCommandStatus.PENDING.value
        leased = RunExecutionCommandRow.lease_expires_at > now
        statement = select(
            func.count().filter(pending).label("pending"),
            func.count().filter(*_claimable(now)).label("ready"),
            func.count().filter(pending, leased).label("leased"),
            func.count()
            .filter(RunExecutionCommandRow.status == ExecutionCommandStatus.DISPATCHED.value)
            .label("dispatched"),
            func.min(case((pending, RunExecutionCommandRow.created_at))).label("oldest"),
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).one()
        oldest: datetime | None = row.oldest
        return RunExecutionCommandBacklog(
            pending=int(row.pending),
            ready=int(row.ready),
            leased=int(row.leased),
            dispatched=int(row.dispatched),
            oldest_pending_age_seconds=(
                None if oldest is None else max(0.0, (now - oldest).total_seconds())
            ),
        )


class PostgresRunAcceptance:
    """Write Run, ``run.queued`` event and dispatch command as one fact.

    Nothing external is called here: the transaction commits and returns, and
    the Dispatcher is what later touches Redis. That ordering is the whole
    point, because a process that dies right after the commit has still left a
    complete, recoverable record of what the platform accepted.
    """

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def accept(
        self,
        run: Run,
        event: RunEvent,
        command: RunExecutionCommand | None,
    ) -> None:
        _require_matching_event(run, event)
        async with self._sessions() as session:
            session.add(build_run_row(run))
            session.add(build_event_row(event))
            if command is not None:
                _require_matching_command(run, command)
                session.add(build_command_row(command))
            try:
                await session.commit()
            except IntegrityError as error:
                # The Run row is inserted in this same transaction, so a unique
                # violation is the idempotency arbitration losing the race.
                await session.rollback()
                raise ConflictError(f"run already exists: {run.run_id}") from error

    async def ensure_command(self, command: RunExecutionCommand) -> bool:
        async with self._sessions() as session:
            session.add(build_command_row(command))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False


def _require_matching_event(run: Run, event: RunEvent) -> None:
    if (event.tenant_id, event.run_id) != (run.tenant_id, run.run_id):
        raise ValueError(
            "acceptance event does not belong to the accepted run: "
            f"{event.tenant_id}/{event.run_id}"
        )


def _require_matching_command(run: Run, command: RunExecutionCommand) -> None:
    if (command.tenant_id, command.run_id) != (run.tenant_id, run.run_id):
        raise ValueError(
            "dispatch command does not belong to the accepted run: "
            f"{command.tenant_id}/{command.run_id}"
        )
    if command.session_id != run.session_id:
        raise ValueError(
            f"dispatch command session does not match the run: {command.command_id}"
        )
