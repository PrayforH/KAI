"""Desktop keyboard/pointer ownership between an owner and their assistant.

The execution lease decides who may run commands. This service decides who may
touch the visible desktop. State lives in one locked row per computer:

    observe -> agent | takeover_pending -> human
    human   -> observe (return) | observe (TTL expiry, lazy)
    agent   -> takeover_pending (user override) | observe (release/expiry, lazy)

Every transition that changes who may send input bumps ``epoch``; each GUI
action binds the epoch it observed, so a stale sender cannot act on a desktop
it no longer owns. ``takeover_pending`` blocks new input on both sides while
the node stops and drains the actions it still holds; only the node's drain
acknowledgement publishes ``human`` (``confirm_human``). A pending window
that expires without that acknowledgement falls back to observation, so a
takeover can never succeed on the strength of a timer alone.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from harness.computer.binding import desktop_binding
from harness.computer.storage import ComputerDesktopLeaseRow as Row
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory

DRAIN_WINDOW_SECONDS = 5
DRAIN_POLL_SECONDS = 0.2
HUMAN_TTL_SECONDS = 300
AGENT_TTL_SECONDS = 120


class DesktopState(StrEnum):
    OBSERVE = "observe"
    AGENT = "agent"
    HUMAN = "human"
    TAKEOVER_PENDING = "takeover_pending"


class DesktopControl(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    tenant_id: str
    user_id: str
    computer_id: str
    state: DesktopState
    owner: str | None
    epoch: int
    revision: int
    expires_at: datetime | None
    binding_key: str | None = None

    def input_allowed(self, *, kind: str, epoch: int | None = None) -> bool:
        if kind == "agent":
            return self.state is DesktopState.AGENT and (epoch is None or epoch == self.epoch)
        if kind == "human":
            return self.state is DesktopState.HUMAN
        return False


class DesktopOccupiedError(ConflictError):
    """The desktop is not in the state the caller needs."""


class DesktopControlService:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def view(self, tenant_id: str, user_id: str) -> DesktopControl:
        async with self._sessions.begin() as session:
            row = await self._row(session, tenant_id, user_id)
            settled = self._settle(row, datetime.now(UTC))
            if settled:
                session.add(row)
            binding = await self._binding(session, row)
            return DesktopControl.model_validate(row).model_copy(update={"binding_key": binding})

    async def acquire_agent(self, tenant_id: str, user_id: str, *, owner: str) -> DesktopControl:
        """First GUI use of a Run claims input ownership from observe."""

        _identity(owner)
        async with self._sessions.begin() as session:
            moment = datetime.now(UTC)
            row = await self._row(session, tenant_id, user_id)
            self._settle(row, moment)
            if row.state == DesktopState.AGENT.value and row.owner == owner:
                row.expires_at = moment + timedelta(seconds=AGENT_TTL_SECONDS)
                row.revision += 1
                row.updated_at = moment
                return DesktopControl.model_validate(row)
            if row.state != DesktopState.OBSERVE:
                raise DesktopOccupiedError(self._occupied(row))
            row.state = DesktopState.AGENT.value
            row.owner = owner
            row.epoch += 1
            row.revision += 1
            row.expires_at = moment + timedelta(seconds=AGENT_TTL_SECONDS)
            row.updated_at = moment
            return DesktopControl.model_validate(row)

    async def renew_agent(self, tenant_id: str, user_id: str, *, owner: str) -> DesktopControl:
        """Extend an existing agent ownership; never grants from observation."""

        _identity(owner)
        async with self._sessions.begin() as session:
            moment = datetime.now(UTC)
            row = await self._row(session, tenant_id, user_id)
            self._settle(row, moment)
            if row.state != DesktopState.AGENT.value or row.owner != owner:
                raise DesktopOccupiedError(self._occupied(row))
            row.expires_at = moment + timedelta(seconds=AGENT_TTL_SECONDS)
            row.revision += 1
            row.updated_at = moment
            return DesktopControl.model_validate(row)

    async def release_agent(self, tenant_id: str, user_id: str, *, owner: str) -> DesktopControl:
        """Run teardown returns the desktop to observation, best effort."""

        async with self._sessions.begin() as session:
            row = await self._row(session, tenant_id, user_id)
            if row.state == DesktopState.AGENT.value and row.owner == owner:
                moment = datetime.now(UTC)
                row.state = DesktopState.OBSERVE.value
                row.owner = None
                row.epoch += 1
                row.revision += 1
                row.expires_at = None
                row.updated_at = moment
            return DesktopControl.model_validate(row)

    @staticmethod
    async def _run_executing(session: AsyncSession, tenant_id: str, user_id: str) -> bool:
        """A Run holding the execution lease is the only writer left to drain."""

        from harness.computer.storage import ComputerLeaseRow

        count = await session.scalar(
            select(func.count())
            .select_from(ComputerLeaseRow)
            .where(
                ComputerLeaseRow.tenant_id == tenant_id,
                ComputerLeaseRow.user_id == user_id,
                ComputerLeaseRow.state == "active",
                ComputerLeaseRow.run_id.is_not(None),
                ComputerLeaseRow.expires_at > func.clock_timestamp(),
            )
        )
        return bool(count)

    async def request_human(
        self, tenant_id: str, user_id: str, *, expected_binding: str | None = None
    ) -> DesktopControl:
        """Takeover blocks both sides until the node confirms the drain."""

        async with self._sessions.begin() as session:
            moment = datetime.now(UTC)
            row = await self._row(session, tenant_id, user_id)
            await self._check_binding(session, row, expected_binding)
            self._settle(row, moment)
            if row.state == DesktopState.HUMAN.value:
                row.expires_at = moment + timedelta(seconds=HUMAN_TTL_SECONDS)
                row.revision += 1
                row.updated_at = moment
                return DesktopControl.model_validate(row)
            if row.state == DesktopState.TAKEOVER_PENDING:
                return DesktopControl.model_validate(row)
            if await self._run_executing(session, row.tenant_id, row.user_id):
                raise DesktopOccupiedError(
                    "助理正在这台电脑上执行任务，等它结束或先中止后再取得控制权。"
                )
            row.state = DesktopState.TAKEOVER_PENDING.value
            row.owner = None
            row.epoch += 1
            row.revision += 1
            row.expires_at = moment + timedelta(seconds=DRAIN_WINDOW_SECONDS)
            row.updated_at = moment
            return DesktopControl.model_validate(row)

    async def confirm_human(
        self, tenant_id: str, user_id: str, *, expected_epoch: int
    ) -> DesktopControl:
        """Publish human ownership once, guarded by the pending epoch.

        The node's drain acknowledgement carries the epoch it observed while
        pending. A stale acknowledgement — after the window fell back to
        observation or the user re-requested takeover — must not publish an
        old drain over newer state, so the epoch acts as the CAS token.
        """

        async with self._sessions.begin() as session:
            moment = datetime.now(UTC)
            row = await self._row(session, tenant_id, user_id)
            if row.state == DesktopState.HUMAN.value:
                row.expires_at = moment + timedelta(seconds=HUMAN_TTL_SECONDS)
                row.revision += 1
                row.updated_at = moment
                return DesktopControl.model_validate(row)
            if row.state != DesktopState.TAKEOVER_PENDING.value or row.epoch != expected_epoch:
                return DesktopControl.model_validate(row)
            row.state = DesktopState.HUMAN.value
            row.owner = None
            row.epoch += 1
            row.revision += 1
            row.expires_at = moment + timedelta(seconds=HUMAN_TTL_SECONDS)
            row.updated_at = moment
            return DesktopControl.model_validate(row)

    async def release_human(
        self, tenant_id: str, user_id: str, *, expected_binding: str | None = None
    ) -> DesktopControl:
        async with self._sessions.begin() as session:
            row = await self._row(session, tenant_id, user_id)
            await self._check_binding(session, row, expected_binding)
            if row.state in {DesktopState.HUMAN.value, DesktopState.TAKEOVER_PENDING.value}:
                moment = datetime.now(UTC)
                row.state = DesktopState.OBSERVE.value
                row.epoch += 1
                row.revision += 1
                row.expires_at = None
                row.updated_at = moment
            return DesktopControl.model_validate(row)

    async def refresh_human(
        self, tenant_id: str, user_id: str, *, expected_binding: str | None = None
    ) -> DesktopControl:
        async with self._sessions.begin() as session:
            row = await self._row(session, tenant_id, user_id)
            await self._check_binding(session, row, expected_binding)
            if row.state == DesktopState.HUMAN.value:
                moment = datetime.now(UTC)
                row.expires_at = moment + timedelta(seconds=HUMAN_TTL_SECONDS)
                row.revision += 1
                row.updated_at = moment
            return DesktopControl.model_validate(row)

    @staticmethod
    def _settle(row: Row, moment: datetime) -> bool:
        """Lazy transitions for expired pending/human/agent rows.

        Every expiry lands back in observation: a pending window whose drain
        was never confirmed is a failed takeover, not a success, and an
        expired human or agent ownership is an abandoned one. The epoch bump
        keeps every stale sender fenced across the fall-back.
        """

        expiry = row.expires_at
        if expiry is None or expiry > moment:
            return False
        row.state = DesktopState.OBSERVE.value
        row.owner = None
        row.epoch += 1
        row.revision += 1
        row.expires_at = None
        row.updated_at = moment
        return True

    @staticmethod
    def _occupied(row: Row) -> str:
        if row.state == DesktopState.TAKEOVER_PENDING.value:
            return "桌面正在切换控制权，请稍后重试。"
        if row.state == DesktopState.HUMAN.value:
            return ("用户正在控制电脑；请在电脑面板点击“交还助理”后再试，"
                    "不要通过关闭窗口或切换标签页交还。")
        return "桌面控制权状态异常，请稍后重试。"

    @staticmethod
    async def _binding(session: AsyncSession, row: Row) -> str:
        from harness.computer.storage import AssistantComputerRow as Computer

        # The desktop row stays locked throughout this read. A provider switch
        # needs that same lock, so target identity and epoch cannot tear apart.
        computer = await session.scalar(
            select(Computer).where(
                Computer.tenant_id == row.tenant_id,
                Computer.user_id == row.user_id,
            )
        )
        if computer is None:
            raise NotFoundError("owner computer not found")
        return desktop_binding(computer.provider, computer.image, computer.volume_ref)

    async def _check_binding(self, session: AsyncSession, row: Row, expected: str | None) -> None:
        if expected is not None and await self._binding(session, row) != expected:
            raise DesktopOccupiedError("查看的电脑不是当前助理使用的电脑，请刷新后重试。")

    async def _row(self, session: AsyncSession, tenant_id: str, user_id: str) -> Row:
        from harness.computer.storage import AssistantComputerRow as Computer

        found = await session.scalar(
            select(Row).where(Row.tenant_id == tenant_id, Row.user_id == user_id).with_for_update()
        )
        row: Row | None = found
        if row is not None:
            return row
        computer: str | None = await session.scalar(
            select(Computer.computer_id).where(
                Computer.tenant_id == tenant_id, Computer.user_id == user_id
            )
        )
        if computer is None:
            raise NotFoundError("owner computer not found")
        moment = datetime.now(UTC)
        row = Row(
            tenant_id=tenant_id,
            user_id=user_id,
            computer_id=computer,
            state=DesktopState.OBSERVE.value,
            owner=None,
            epoch=0,
            revision=1,
            expires_at=None,
            created_at=moment,
            updated_at=moment,
        )
        session.add(row)
        await session.flush()
        return row


def _identity(value: str) -> None:
    if not value.strip() or len(value) > 160:
        raise ValueError("desktop owner identity is invalid")
