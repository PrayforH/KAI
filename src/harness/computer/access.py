"""Canonical account, personal-thread and Worker ownership checks for Computers.

A background objective created by the personal assistant inherits a narrow
capability on the owner's own computer: its execution run is authorized only
while that objective is still active, and never beyond the computer the user
already owns. Nothing else opens for ordinary task sessions.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.assistant.models import Objective, Specialist
from harness.assistant.permissions import require_local_tools
from harness.assistant.storage import ObjectiveRow, SpecialistRow
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    LeasePurpose,
)
from harness.computer.storage import ComputerLeaseRow
from harness.core.errors import ConflictError
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


def _bound(
    session: Session,
    row: AguiThreadBindingRow,
    thread_id: str = PERSONAL_ASSISTANT_THREAD,
) -> bool:
    binding = AguiThreadBinding.model_validate(row.payload)
    return (
        binding.tenant_id == row.tenant_id == session.tenant_id
        and binding.user_id == row.user_id == session.user_id
        and binding.thread_id == row.thread_id == thread_id
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

    @staticmethod
    def _session_binding(session_id: str | InstrumentedAttribute[str]) -> ColumnElement[bool]:
        return or_(
            AguiThreadBindingRow.session_id == session_id,
            cast(AguiThreadBindingRow.payload["previous_session_ids"], JSONB).op("?")(session_id),
        )

    async def _specialist_bound(
        self,
        db: AsyncSession,
        session: Session,
        binding: AguiThreadBindingRow,
    ) -> bool:
        if not _bound(session, binding, binding.thread_id):
            return False
        if binding.thread_id.startswith("assistant_specialist_"):
            specialist_id = binding.thread_id.removeprefix("assistant_")
        elif binding.thread_id.startswith("assistant_objective_"):
            # Older Bots keep their original duty conversation. Resolve its
            # persisted owner/contact relationship instead of moving history
            # or granting arbitrary managed task/project threads access.
            objective_row = await db.get(
                ObjectiveRow, binding.thread_id.removeprefix("assistant_")
            )
            if (objective_row is None or objective_row.tenant_id != session.tenant_id
                    or objective_row.user_id != session.user_id):
                return False
            objective = Objective.model_validate(objective_row.payload)
            if (objective.objective_id != objective_row.objective_id
                    or objective.tenant_id != objective_row.tenant_id
                    or objective.user_id != objective_row.user_id
                    or objective.status.value != objective_row.status
                    or objective.status.value != "active"
                    or objective.project_id is not None
                    or objective.managed_thread_id is not None
                    or objective.specialist_id is None):
                return False
            specialist_id = objective.specialist_id
        else:
            return False
        row = await db.get(SpecialistRow, specialist_id)
        if row is None or row.tenant_id != session.tenant_id or row.user_id != session.user_id:
            return False
        bot = Specialist.model_validate(row.payload)
        if (
            bot.specialist_id != row.specialist_id
            or bot.tenant_id != row.tenant_id
            or bot.user_id != row.user_id
            or bot.status.value != row.status
            or row.status in {"paused", "archived"}
            or bot.deleted_at is not None
            or bot.config.get("execution_environment") != "personal_computer"
            or bot.config.get("conversation_thread_id", binding.thread_id) != binding.thread_id
        ):
            return False
        try:
            require_local_tools(bot.config)
        except ConflictError:
            return False
        return True

    async def _specialist_session(self, db: AsyncSession, session: Session) -> bool:
        rows = list(
            await db.scalars(
                select(AguiThreadBindingRow)
                .where(
                    AguiThreadBindingRow.tenant_id == session.tenant_id,
                    AguiThreadBindingRow.user_id == session.user_id,
                    or_(
                        AguiThreadBindingRow.thread_id.startswith("assistant_specialist_"),
                        AguiThreadBindingRow.thread_id.startswith("assistant_objective_"),
                    ),
                    self._session_binding(session.session_id),
                )
                .limit(2)
            )
        )
        if len(rows) != 1 or not await self._specialist_bound(db, session, rows[0]):
            return False
        stored = await db.get(SessionRow, (session.tenant_id, session.session_id))
        if (
            stored is None
            or stored.user_id != session.user_id
            or Session.model_validate(stored.payload) != session
        ):
            raise _denied()
        return True

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
                return await self._specialist_session(db, session)
            record = AguiThreadBinding.model_validate(binding.payload)
            if session.session_id not in record.session_ids:
                return await self._specialist_session(db, session)
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
            if await self._delegated_chain(db, computer, lease, operation, allowed):
                return
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
                            or_(
                                AguiThreadBindingRow.thread_id == PERSONAL_ASSISTANT_THREAD,
                                AguiThreadBindingRow.thread_id.startswith("assistant_specialist_"),
                                AguiThreadBindingRow.thread_id.startswith("assistant_objective_"),
                            ),
                            self._session_binding(SessionRow.session_id),
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
                        SandboxLeaseRow.provider == computer.provider,
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
                not (_bound(session, binding) or await self._specialist_bound(db, session, binding))
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

    async def _delegated_chain(
        self,
        db: Any,
        computer: AssistantComputer,
        lease: ComputerLease,
        operation: str,
        allowed: set[str],
    ) -> bool:
        """The narrow background-objective capability on the owner's computer.

        One live objective per owner at a time grants its execution run the
        same computer the owner already has, for exactly the purposes the run
        requested. The objective going inactive (paused, completed,
        cancelled) or the objective thread being archived closes the grant on
        the very next action; nothing else about the chain relaxes.
        """

        rows = (
            (
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
                            AguiThreadBindingRow.thread_id != PERSONAL_ASSISTANT_THREAD,
                        ),
                    )
                    .join(
                        ObjectiveRow,
                        and_(
                            ObjectiveRow.tenant_id == computer.tenant_id,
                            ObjectiveRow.user_id == computer.user_id,
                            ObjectiveRow.status == "active",
                            # The objective that owns exactly this thread.
                            ObjectiveRow.payload["threadId"].as_string()
                            == AguiThreadBindingRow.thread_id,
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
                        SandboxLeaseRow.provider == computer.provider,
                        SandboxLeaseRow.state == "active",
                        SandboxLeaseRow.expires_at > func.clock_timestamp(),
                        SessionRow.user_id == computer.user_id,
                        UserRow.disabled.is_(False),
                        RunRow.status.in_(allowed),
                    )
                    .limit(2)
                )
            )
            .all()
        )
        if len(rows) != 1:
            return False
        _sandbox, session_row, run_row, binding, run_fence = rows[0]
        record = AguiThreadBinding.model_validate(binding.payload)
        if record.archived_at is not None:
            return False
        session = Session.model_validate(session_row.payload)
        run = Run.model_validate(run_row.payload)
        if (
            record.tenant_id != session.tenant_id
            or record.user_id != session.user_id
            or session.session_id not in record.session_ids
            or session.tenant_id != computer.tenant_id
            or session.user_id != computer.user_id
            or run.run_id != run_row.run_id
            or run.session_id != session.session_id
            or run.tenant_id != computer.tenant_id
            or run.fencing_token != run_row.fencing_token
            or run.status.value != run_row.status
        ):
            return False
        if lease.purpose is LeasePurpose.EXECUTE and (
            lease.run_id != run.run_id
            or lease.session_id != session.session_id
            or run_fence is None
            or run_fence != run.fencing_token
        ):
            return False
        return True
