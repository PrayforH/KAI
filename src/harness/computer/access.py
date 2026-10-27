"""Canonical account, personal-thread and Worker ownership checks for Computers."""

from collections.abc import Mapping
from typing import Protocol

from sqlalchemy import and_, func, select

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    LeasePurpose,
)
from harness.computer.storage import ComputerLeaseRow
from harness.core.models import AguiThreadBinding, Run, Session
from harness.storage.database import SessionFactory
from harness.storage.models import (
    AguiThreadBindingRow,
    RunRow,
    SandboxLeaseRow,
    SessionRow,
    TenantMembershipRow,
    UserRow,
)


class ComputerAuthorizer(Protocol):
    async def authorize(
        self, computer: AssistantComputer, lease: ComputerLease, operation: str
    ) -> None: ...


def _denied() -> ComputerLeaseLostError:
    return ComputerLeaseLostError("Computer account, conversation or execution access was lost")


def _bound(session: Session, row: AguiThreadBindingRow) -> bool:
    binding = AguiThreadBinding.model_validate(row.payload)
    return (
        binding.tenant_id == row.tenant_id == session.tenant_id
        and binding.user_id == row.user_id == session.user_id
        and binding.thread_id == row.thread_id == PERSONAL_ASSISTANT_THREAD
        and binding.session_id == row.session_id
        and session.session_id in binding.session_ids
        and binding.project_id is None
        and row.project_id is None
        and binding.archived_at is None
        and session.environment != "preview"
    )


class CanonicalComputerAccess:
    def __init__(self, sessions: SessionFactory, owners: Mapping[str, frozenset[str]]) -> None:
        self.sessions, self.owners = sessions, owners

    async def is_personal_session(self, session: Session) -> bool:
        """A normal task is not selected merely because its user owns a computer."""
        if session.user_id not in self.owners.get(session.tenant_id, frozenset()):
            return False
        async with self.sessions() as db:
            user = await db.get(UserRow, session.user_id)
            membership = await db.get(TenantMembershipRow, (session.tenant_id, session.user_id))
            if user is None or user.disabled or membership is None:
                raise _denied()
            binding = await db.get(
                AguiThreadBindingRow,
                (session.tenant_id, session.user_id, PERSONAL_ASSISTANT_THREAD),
            )
            if binding is None:
                return False
            record = AguiThreadBinding.model_validate(binding.payload)
            if session.session_id not in record.session_ids:
                return False
            stored = await db.get(SessionRow, (session.tenant_id, session.session_id))
            if (
                not _bound(session, binding)
                or stored is None
                or stored.user_id != session.user_id
                or Session.model_validate(stored.payload) != session
            ):
                raise _denied()
        return True

    async def authorize(
        self, computer: AssistantComputer, lease: ComputerLease, operation: str
    ) -> None:
        # Cleanup only stops the validated current holder/instance. Revocation
        # must not prevent stopping it; this path never reads user file content.
        if operation in {"deactivate", "reset"}:
            return
        if computer.user_id not in self.owners.get(computer.tenant_id, frozenset()):
            raise _denied()
        if operation == "upgrade":
            # Maintenance has no live model Run. It needs the same active
            # account and personal conversation, plus the host's recovery gate.
            if lease.purpose is not LeasePurpose.RECOVER:
                raise _denied()
            async with self.sessions() as db:
                user = await db.get(UserRow, computer.user_id)
                member = await db.get(TenantMembershipRow, (computer.tenant_id, computer.user_id))
                binding = await db.get(
                    AguiThreadBindingRow,
                    (computer.tenant_id, computer.user_id, PERSONAL_ASSISTANT_THREAD),
                )
                stored = (
                    await db.get(SessionRow, (computer.tenant_id, binding.session_id))
                    if binding
                    else None
                )
                if (
                    user is None
                    or user.disabled
                    or member is None
                    or binding is None
                    or stored is None
                    or stored.user_id != computer.user_id
                ):
                    raise _denied()
                session = Session.model_validate(stored.payload)
                if session.session_id != stored.session_id or not _bound(session, binding):
                    raise _denied()
            return
        allowed = (
            {"provisioning", "running", "waiting_approval"}
            if operation == "renew"
            else {"provisioning", "running"}
        )
        async with self.sessions() as db:
            rows = (
                await db.execute(
                    select(
                        SandboxLeaseRow,
                        SessionRow,
                        RunRow,
                        AguiThreadBindingRow,
                        ComputerLeaseRow.run_fencing_token,
                    )
                    .select_from(SandboxLeaseRow)
                    .join(
                        SessionRow,
                        and_(
                            SessionRow.tenant_id == SandboxLeaseRow.tenant_id,
                            SessionRow.session_id == SandboxLeaseRow.session_id,
                        ),
                    )
                    .join(
                        RunRow,
                        and_(
                            RunRow.tenant_id == SandboxLeaseRow.tenant_id,
                            RunRow.run_id == SandboxLeaseRow.run_id,
                            RunRow.session_id == SandboxLeaseRow.session_id,
                        ),
                    )
                    .join(UserRow, UserRow.user_id == SessionRow.user_id)
                    .join(
                        TenantMembershipRow,
                        and_(
                            TenantMembershipRow.tenant_id == SessionRow.tenant_id,
                            TenantMembershipRow.user_id == SessionRow.user_id,
                        ),
                    )
                    .join(
                        AguiThreadBindingRow,
                        and_(
                            AguiThreadBindingRow.tenant_id == SessionRow.tenant_id,
                            AguiThreadBindingRow.user_id == SessionRow.user_id,
                            AguiThreadBindingRow.thread_id == PERSONAL_ASSISTANT_THREAD,
                        ),
                    )
                    .join(ComputerLeaseRow, ComputerLeaseRow.lease_id == lease.lease_id)
                    .where(
                        ComputerLeaseRow.tenant_id == computer.tenant_id,
                        ComputerLeaseRow.computer_id == computer.computer_id,
                        ComputerLeaseRow.epoch == lease.epoch,
                        ComputerLeaseRow.owner == lease.owner,
                        ComputerLeaseRow.state == "active",
                        ComputerLeaseRow.expires_at > func.clock_timestamp(),
                        SandboxLeaseRow.tenant_id == computer.tenant_id,
                        SandboxLeaseRow.sandbox_id == lease.owner,
                        SandboxLeaseRow.provider == "docker-computer",
                        SandboxLeaseRow.state == "active",
                        SandboxLeaseRow.expires_at > func.clock_timestamp(),
                        SessionRow.user_id == computer.user_id,
                        UserRow.disabled.is_(False),
                        RunRow.status.in_(allowed),
                    )
                    .limit(2)
                )
            ).all()
            if len(rows) != 1:
                raise _denied()
            sandbox, session_row, run_row, binding, run_fence = rows[0]
            session = Session.model_validate(session_row.payload)
            run = Run.model_validate(run_row.payload)
            if (
                not _bound(session, binding)
                or session.session_id != session_row.session_id
                or session.tenant_id != computer.tenant_id
                or session.user_id != computer.user_id
                or run.run_id != run_row.run_id
                or run.session_id != session.session_id
                or run.tenant_id != computer.tenant_id
                or run.fencing_token != run_row.fencing_token
                or run.status.value != run_row.status
            ):
                raise _denied()
            latest = (
                (
                    await db.execute(
                        select(SandboxLeaseRow)
                        .where(
                            SandboxLeaseRow.tenant_id == computer.tenant_id,
                            SandboxLeaseRow.session_id == session.session_id,
                        )
                        .order_by(SandboxLeaseRow.epoch.desc(), SandboxLeaseRow.created_at.desc())
                        .limit(2)
                    )
                )
                .scalars()
                .all()
            )
            if (
                not latest
                or latest[0].lease_id != sandbox.lease_id
                or (len(latest) > 1 and latest[0].epoch == latest[1].epoch)
            ):
                raise _denied()
            if lease.purpose is LeasePurpose.EXECUTE and (
                lease.run_id != run.run_id
                or lease.session_id != session.session_id
                or run_fence is None
                or run_fence != run.fencing_token
            ):
                raise _denied()
