"""Postgres-backed extraction jobs with leases and successful-run reconciliation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert

from harness.core.errors import ConflictError
from harness.core.models import Run, Session
from harness.memory_bank.extraction import MemoryExtractor
from harness.memory_bank.models import MemorySourceKind, MemoryStatus
from harness.memory_bank.service import MemoryBankService
from harness.storage.database import SessionFactory
from harness.storage.models import MemoryExtractionJobRow, RunRow, SessionRow


class MemoryProcessingController:
    def __init__(
        self,
        sessions: SessionFactory,
        bank: MemoryBankService,
        extractor: MemoryExtractor,
        *,
        since: datetime,
    ) -> None:
        self._sessions = sessions
        self._bank = bank
        self._extractor = extractor
        self._since = since

    async def enqueue_completed(self) -> int:
        # Reconciliation closes the crash gap between terminal Run commit and
        # extraction enqueue. Only runs after the configured rollout date qualify.
        j = MemoryExtractionJobRow
        query = (
            select(RunRow, SessionRow)
            .join(
                SessionRow,
                and_(
                    SessionRow.tenant_id == RunRow.tenant_id,
                    SessionRow.session_id == RunRow.session_id,
                ),
            )
            .outerjoin(j, and_(j.tenant_id == RunRow.tenant_id, j.run_id == RunRow.run_id))
            .where(
                RunRow.status == "succeeded",
                RunRow.updated_at >= self._since,
                j.run_id.is_(None),
            )
            .order_by(RunRow.updated_at)
            .limit(50)
        )
        count = 0
        async with self._sessions() as db:
            for run_row, session_row in (await db.execute(query)).all():
                run = Run.model_validate(run_row.payload)
                session = Session.model_validate(session_row.payload)
                status = (
                    "skipped"
                    if run.input.get("eval_run_id") or session.environment == "preview"
                    else "pending"
                )
                await db.execute(
                    insert(j)
                    .values(
                        tenant_id=run.tenant_id,
                        run_id=run.run_id,
                        user_id=session.user_id,
                        agent_name=session.agent_name,
                        agent_owner_user_id=session.resolved_agent_owner_user_id,
                        session_id=session.session_id,
                        status=status,
                        attempts=0,
                        created_at=run.created_at,
                        available_at=datetime.now(UTC),
                        error_code=None,
                    )
                    .on_conflict_do_nothing()
                )
                count += 1
            await db.commit()
        return count

    async def process_once(self) -> int:
        await self.enqueue_completed()
        now = datetime.now(UTC)
        j = MemoryExtractionJobRow
        async with self._sessions() as db:
            job = await db.scalar(
                select(j)
                .where(
                    j.status.in_(["pending", "retrying", "processing"]),
                    j.available_at <= now,
                    j.attempts < 5,
                )
                .order_by(j.available_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                # Expired fifth attempts must become terminal, not stay processing.
                await db.execute(
                    update(j)
                    .where(j.status == "processing", j.attempts >= 5, j.available_at <= now)
                    .values(status="failed", error_code="lease_expired")
                )
                await db.commit()
                return 0
            job.status = "processing"
            job.attempts += 1
            job.available_at = now + timedelta(minutes=5)
            await db.commit()
        status, error_code = "completed", None
        try:
            async with self._sessions() as db:
                row = await db.get(RunRow, (job.tenant_id, job.run_id))
                if row is None:
                    raise ValueError("source run removed")
                run = Run.model_validate(row.payload)
            prompt = str(run.input.get("prompt", "")).strip()
            if not prompt:
                candidates = []
                existing = []
            else:
                hits = await self._bank.search(
                    job.tenant_id,
                    job.user_id,
                    job.agent_name,
                    prompt[:4000],
                    owner_id=job.agent_owner_user_id,
                    limit=12,
                )
                pending = await self._bank.repository.list_entries(
                    job.tenant_id,
                    job.user_id,
                    agent_name=job.agent_name,
                    owner_id=job.agent_owner_user_id,
                    statuses=frozenset({MemoryStatus.PENDING}),
                    limit=4,
                )
                existing = [hit.entry for hit in hits[: 12 - len(pending)]] + list(pending)
                candidates = await self._extractor.extract(prompt, existing)
            previous = {entry.entry_id: entry for entry in existing}
            for candidate in candidates:
                try:
                    await self._bank.propose(
                        tenant_id=job.tenant_id,
                        user_id=job.user_id,
                        agent_name=job.agent_name,
                        owner_id=job.agent_owner_user_id,
                        content=candidate.content,
                        memory_type=candidate.memory_type,
                        topic=candidate.topic,
                        conditions=candidate.conditions,
                        evidence=candidate.evidence,
                        supersedes=candidate.supersedes,
                        supersedes_version=(
                            previous[candidate.supersedes].version if candidate.supersedes else None
                        ),
                        source_kind=MemorySourceKind.AGENT,
                        source_label="会话自动提取 · 用户原文",
                        confidence=0.7,
                        run_id=job.run_id,
                        session_id=job.session_id,
                        captured_at=run.created_at,
                        extraction_job_id=job.run_id,
                        extraction_version=job.attempts,
                    )
                except ConflictError:
                    # Safety/duplicate/changed target/cancelled lease: never force a write.
                    continue
        except Exception as error:
            status = "failed" if job.attempts >= 5 else "retrying"
            error_code = "memory_extraction_" + type(error).__name__
            logging.getLogger(__name__).warning("memory extraction retry: %s", type(error).__name__)
        async with self._sessions() as db:
            await db.execute(
                update(j)
                .where(
                    j.tenant_id == job.tenant_id,
                    j.run_id == job.run_id,
                    j.status == "processing",
                    j.attempts == job.attempts,
                )
                .values(
                    status=status,
                    error_code=error_code,
                    available_at=datetime.now(UTC)
                    + timedelta(seconds=min(300, 15 * 2**job.attempts)),
                )
            )
            await db.commit()
        return 1
