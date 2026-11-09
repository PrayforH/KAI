"""Move one account's active Bot configuration to its general assistant.

Run with the application's production environment. The default is a read-only
inventory. Apply only after pausing request ingress and draining the worker
and assistant scheduler. Existing Run, Session and thread IDs remain valid.
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select, text

from harness.application.agents import AgentService
from harness.application.sessions import SessionService
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.assistant.models import (
    Objective,
    ObjectiveExecutionStatus,
    ObjectiveStatus,
    Specialist,
)
from harness.assistant.storage import ObjectiveExecutionRow, ObjectiveRow, SpecialistRow
from harness.composition import build_production_container
from harness.config import Settings
from harness.core.errors import ConflictError
from harness.core.models import AguiThreadBinding, RunStatus, Session
from harness.storage.database import SessionFactory, create_database
from harness.storage.models import AguiThreadBindingRow, RunRow, SessionRow


@dataclass(frozen=True)
class MigrationCounts:
    bots: int
    duties: int
    threads: int


async def migrate(
    database: SessionFactory,
    tenant: str,
    owner: str,
    *,
    services: tuple[AgentService, SessionService] | None = None,
) -> MigrationCounts:
    """Switch the live coordinates together; keep previous sessions in history."""
    async with database.begin() as db:
        if services is not None:
            # The release procedure drains writers. Lock the affected tables
            # during the final checks and pointer updates as a second guard.
            await db.execute(text(
                "LOCK TABLE agui_thread_bindings, assistant_specialists, "
                "assistant_objectives, assistant_executions, runs IN SHARE ROW EXCLUSIVE MODE"
            ))
        bots = [
            (row, Specialist.model_validate(row.payload))
            for row in await db.scalars(select(SpecialistRow).where(
                SpecialistRow.tenant_id == tenant,
                SpecialistRow.user_id == owner,
                SpecialistRow.status != "archived",
                SpecialistRow.payload["deletedAt"].as_string().is_(None),
            ))
        ]
        bot_ids = {bot.specialist_id for _, bot in bots}
        duties = [
            (row, Objective.model_validate(row.payload))
            for row in await db.scalars(select(ObjectiveRow).where(
                ObjectiveRow.tenant_id == tenant,
                ObjectiveRow.user_id == owner,
                ObjectiveRow.status.in_((ObjectiveStatus.ACTIVE, ObjectiveStatus.PAUSED)),
                ObjectiveRow.payload["specialistId"].as_string().in_(bot_ids),
                ObjectiveRow.payload["managedThreadId"].as_string().is_(None),
            ))
        ]
        bot_threads = {PERSONAL_ASSISTANT_THREAD}
        for _, bot in bots:
            thread = bot.config.get("conversation_thread_id", f"assistant_{bot.specialist_id}")
            if isinstance(thread, str):
                bot_threads.add(thread)
        bot_threads.update(f"assistant_{duty.objective_id}" for _, duty in duties)
        bindings = [
            (row, AguiThreadBinding.model_validate(row.payload), Session.model_validate(payload))
            for row, payload in (await db.execute(
                select(AguiThreadBindingRow, SessionRow.payload)
                .join(SessionRow, (SessionRow.tenant_id == AguiThreadBindingRow.tenant_id)
                      & (SessionRow.session_id == AguiThreadBindingRow.session_id)
                      & (SessionRow.user_id == AguiThreadBindingRow.user_id))
                .where(
                    AguiThreadBindingRow.tenant_id == tenant,
                    AguiThreadBindingRow.user_id == owner,
                    AguiThreadBindingRow.thread_id.in_(bot_threads)
                    | ((AguiThreadBindingRow.payload["kind"].as_string() == "bot")
                       & AguiThreadBindingRow.payload["specialist_id"].as_string().in_(bot_ids)),
                )
            )).all()
        ]
        if services is None:
            return MigrationCounts(
                sum(bot.agent_name != "lead-agent" for _, bot in bots),
                sum(duty.agent_name != "lead-agent" for _, duty in duties),
                sum(session.agent_name != "lead-agent" for _, _, session in bindings),
            )

        sessions = {sid for _, binding, _ in bindings for sid in binding.session_ids}
        if await db.scalar(select(RunRow.run_id).where(
            RunRow.tenant_id == tenant,
            RunRow.session_id.in_(sessions),
            RunRow.status.not_in(RunStatus.terminal_statuses()),
        ).limit(1)):
            raise ConflictError("Bot conversation still has an active Run")
        if await db.scalar(select(ObjectiveExecutionRow.execution_id).where(
            ObjectiveExecutionRow.tenant_id == tenant,
            ObjectiveExecutionRow.user_id == owner,
            ObjectiveExecutionRow.objective_id.in_(duty.objective_id for _, duty in duties),
            ObjectiveExecutionRow.status.in_((
                ObjectiveExecutionStatus.PENDING, ObjectiveExecutionStatus.RUNNING,
            )),
        ).limit(1)):
            raise ConflictError("Bot duty still has an active execution")

        agents, session_service = services
        default = await agents.ensure_user_default(tenant, owner)
        if default is None or default.name != "lead-agent":
            raise ConflictError("The account's platform general assistant is unavailable")
        coordinate = (default.name, default.version)
        now = datetime.now(UTC)
        switched = 0
        for row, binding, session in bindings:
            if session.agent_name == default.name:
                continue
            if (
                session.team_ids or session.resolved_agent_owner_user_id != owner
                or session.environment or session.api_key_id
            ):
                raise ConflictError("Bot conversation has a shared or environment-specific Agent")
            key = sha256(f"{session.session_id}\0{default.version}".encode()).hexdigest()[:24]
            new_session = await session_service.create(
                tenant, owner, *coordinate, session_id=f"session_botbase_{key}",
                connection_mode=session.connection_mode,
            )
            row.session_id = new_session.session_id
            row.payload = binding.model_copy(update={
                "session_id": new_session.session_id,
                "previous_session_ids": (*binding.previous_session_ids, binding.session_id),
                "updated_at": now,
            }).model_dump(mode="json")
            switched += 1

        changes = {"agent_name": default.name, "agent_version": default.version, "updated_at": now}
        updated_counts = []
        for items in (bots, duties):
            updated = 0
            for row, item in items:
                if item.agent_name != default.name:
                    row.revision = item.revision + 1
                    row.updated_at = now
                    item = item.model_copy(update=changes | {"revision": row.revision})
                    row.payload = item.model_dump(mode="json", by_alias=True)
                    updated += 1
            updated_counts.append(updated)
        return MigrationCounts(*updated_counts, switched)


async def run(tenant: str, owner: str, *, apply: bool) -> None:
    settings = Settings()
    engine, database = create_database(settings.database_url)
    container = None
    try:
        if apply:
            container = build_production_container(settings, execution_enabled=False)
        counts = await migrate(
            database, tenant, owner,
            services=(container.agents, container.sessions) if container else None,
        )
        print(json.dumps(asdict(counts)))
    finally:
        if container and container.close:
            await container.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.tenant_id, args.user_id, apply=args.apply))
