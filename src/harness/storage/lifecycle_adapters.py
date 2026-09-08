from __future__ import annotations

# SQLAlchemy's generic scalar/delete result types intentionally expose Any at
# this persistence boundary; domain values are validated before leaving it.
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
from dataclasses import dataclass
from typing import cast

import httpx
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from harness.core.models import Run, Session
from harness.core.ports import ArtifactStore
from harness.lifecycle.models import (
    DataLifecycleJob,
    LifecycleJobKind,
    LifecycleScopeKind,
)
from harness.memory_bank.models import MemoryEntry
from harness.quality.models import QualityScore
from harness.storage.database import SessionFactory
from harness.storage.models import (
    AguiThreadBindingRow,
    ApprovalRow,
    ArtifactRow,
    AuditLogRow,
    EvalCaseResultRow,
    EvalDatasetVersionRow,
    EvalRunRow,
    EventRow,
    InputArtifactRow,
    MemoryConsentRow,
    MemoryEntryRow,
    MemoryExtractionJobRow,
    MemoryRetentionRow,
    QualityIncidentRow,
    QualityRuleRow,
    QualityScoreRow,
    RunRow,
    SdkSessionEntryRow,
    SessionContextDigestRow,
    SessionContextStateRow,
    SessionRow,
    ThreadFileRow,
    UserMemoryRow,
    WorkspaceSnapshotRow,
)


@dataclass(frozen=True)
class LifecycleIndex:
    sessions: tuple[Session, ...]
    runs: tuple[Run, ...]
    artifact_ids: tuple[str, ...]
    input_artifact_ids: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    thread_file_ids: tuple[str, ...]
    eval_run_ids: tuple[str, ...]
    eval_dataset_ids: tuple[str, ...]
    trace_ids: tuple[str, ...]


async def lifecycle_index(sessions: SessionFactory, job: DataLifecycleJob) -> LifecycleIndex:
    tenant_id = job.tenant_id
    scope = job.scope
    async with sessions() as db:
        session_rows = (
            await db.scalars(select(SessionRow).where(SessionRow.tenant_id == tenant_id))
        ).all()
        session_values = [Session.model_validate(row.payload) for row in session_rows]
        if scope.kind is LifecycleScopeKind.USER:
            session_values = [item for item in session_values if item.user_id == scope.subject_id]
        elif scope.kind is LifecycleScopeKind.SESSION:
            session_values = [
                item for item in session_values if item.session_id == scope.subject_id
            ]
        elif scope.kind is LifecycleScopeKind.AGENT:
            session_values = [
                item for item in session_values if item.agent_name == scope.subject_id
            ]
        if job.kind is LifecycleJobKind.RETENTION:
            cutoff = job.retention_cutoffs["sessions"]
            session_values = [item for item in session_values if item.created_at < cutoff]
        session_ids = [item.session_id for item in session_values]
        run_rows = (
            (
                await db.scalars(
                    select(RunRow).where(
                        RunRow.tenant_id == tenant_id,
                        RunRow.session_id.in_(session_ids),
                    )
                )
            ).all()
            if session_ids
            else []
        )
        runs = [Run.model_validate(row.payload) for row in run_rows]
        run_ids = [item.run_id for item in runs]
        artifact_rows = (
            (
                await db.scalars(
                    select(ArtifactRow).where(
                        ArtifactRow.tenant_id == tenant_id,
                        ArtifactRow.run_id.in_(run_ids),
                    )
                )
            ).all()
            if run_ids
            else []
        )
        snapshot_rows = (
            (
                await db.scalars(
                    select(WorkspaceSnapshotRow).where(
                        WorkspaceSnapshotRow.tenant_id == tenant_id,
                        WorkspaceSnapshotRow.session_id.in_(session_ids),
                    )
                )
            ).all()
            if session_ids
            else []
        )
        file_rows = (
            (
                await db.scalars(
                    select(ThreadFileRow).where(
                        ThreadFileRow.tenant_id == tenant_id,
                        ThreadFileRow.session_id.in_(session_ids),
                    )
                )
            ).all()
            if session_ids
            else []
        )
        referenced_inputs = {
            str(item)
            for run in runs
            for item in cast(list[object], run.input.get("input_artifact_ids", []))
            if isinstance(item, str)
        }
        if scope.kind is LifecycleScopeKind.TENANT:
            input_rows = (
                await db.scalars(
                    select(InputArtifactRow).where(InputArtifactRow.tenant_id == tenant_id)
                )
            ).all()
        elif scope.kind is LifecycleScopeKind.USER:
            input_rows = (
                await db.scalars(
                    select(InputArtifactRow).where(
                        InputArtifactRow.tenant_id == tenant_id,
                        InputArtifactRow.user_id == scope.subject_id,
                    )
                )
            ).all()
        elif referenced_inputs:
            input_rows = (
                await db.scalars(
                    select(InputArtifactRow).where(
                        InputArtifactRow.tenant_id == tenant_id,
                        InputArtifactRow.input_artifact_id.in_(referenced_inputs),
                    )
                )
            ).all()
        else:
            input_rows = []
        eval_filter = [EvalRunRow.tenant_id == tenant_id]
        dataset_filter = [EvalDatasetVersionRow.tenant_id == tenant_id]
        if scope.kind is LifecycleScopeKind.AGENT:
            eval_filter.append(EvalRunRow.agent_name == scope.subject_id)
            dataset_filter.append(EvalDatasetVersionRow.agent_name == scope.subject_id)
        elif scope.kind is not LifecycleScopeKind.TENANT:
            eval_filter.append(EvalRunRow.eval_run_id == "__none__")
            dataset_filter.append(EvalDatasetVersionRow.dataset_id == "__none__")
        eval_rows = (await db.scalars(select(EvalRunRow).where(*eval_filter))).all()
        dataset_rows = (
            await db.scalars(select(EvalDatasetVersionRow).where(*dataset_filter))
        ).all()
        if job.kind is LifecycleJobKind.RETENTION:
            eval_cutoff = job.retention_cutoffs["evals"]
            eval_rows = [row for row in eval_rows if row.created_at < eval_cutoff]
            dataset_rows = [row for row in dataset_rows if row.created_at < eval_cutoff]
        score_filter = [QualityScoreRow.tenant_id == tenant_id]
        if scope.kind is LifecycleScopeKind.AGENT:
            score_filter.append(QualityScoreRow.agent_name == scope.subject_id)
        elif scope.kind is not LifecycleScopeKind.TENANT and run_ids:
            score_filter.append(QualityScoreRow.run_id.in_(run_ids))
        elif scope.kind is not LifecycleScopeKind.TENANT:
            score_filter.append(QualityScoreRow.run_id == "__none__")
        score_rows = (await db.scalars(select(QualityScoreRow).where(*score_filter))).all()
        trace_ids = tuple(
            sorted(
                {
                    QualityScore.model_validate(row.payload).trace_id
                    for row in score_rows
                    if (
                        job.kind is not LifecycleJobKind.RETENTION
                        or row.created_at < job.retention_cutoffs["traces"]
                    )
                }
            )
        )
        return LifecycleIndex(
            sessions=tuple(session_values),
            runs=tuple(runs),
            artifact_ids=tuple(row.artifact_id for row in artifact_rows),
            input_artifact_ids=tuple(row.input_artifact_id for row in input_rows),
            snapshot_ids=tuple(row.snapshot_id for row in snapshot_rows),
            thread_file_ids=tuple(row.file_id for row in file_rows),
            eval_run_ids=tuple(row.eval_run_id for row in eval_rows),
            eval_dataset_ids=tuple(sorted({row.dataset_id for row in dataset_rows})),
            trace_ids=trace_ids,
        )


class ObjectStoreLifecycleAdapter:
    name = "object-store"

    def __init__(self, sessions: SessionFactory, store: ArtifactStore) -> None:
        self._sessions = sessions
        self._store = store

    async def export(self, job: DataLifecycleJob) -> tuple[object, int]:
        index = await lifecycle_index(self._sessions, job)
        objects = [
            *({"kind": "artifact", "id": item} for item in index.artifact_ids),
            *({"kind": "input-artifact", "id": item} for item in index.input_artifact_ids),
            *({"kind": "workspace-snapshot", "id": item} for item in index.snapshot_ids),
        ]
        return {"objects": objects}, len(objects)

    async def delete(self, job: DataLifecycleJob) -> int:
        index = await lifecycle_index(self._sessions, job)
        object_ids = (
            *index.artifact_ids,
            *index.input_artifact_ids,
            *index.snapshot_ids,
        )
        for object_id in object_ids:
            await self._store.delete(job.tenant_id, object_id)
        return len(object_ids)


class SdkSessionLifecycleAdapter:
    name = "sdk-session"

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def export(self, job: DataLifecycleJob) -> tuple[object, int]:
        index = await lifecycle_index(self._sessions, job)
        session_ids = [item.session_id for item in index.sessions]
        async with self._sessions() as db:
            rows = (
                (
                    await db.scalars(
                        select(SdkSessionEntryRow).where(
                            SdkSessionEntryRow.tenant_id == job.tenant_id,
                            SdkSessionEntryRow.session_id.in_(session_ids),
                        )
                    )
                ).all()
                if session_ids
                else []
            )
        payloads = [row.payload for row in rows]
        return {"entries": payloads}, len(payloads)

    async def delete(self, job: DataLifecycleJob) -> int:
        index = await lifecycle_index(self._sessions, job)
        session_ids = [item.session_id for item in index.sessions]
        if not session_ids:
            return 0
        async with self._sessions() as db:
            rows = (
                await db.scalars(
                    select(SdkSessionEntryRow).where(
                        SdkSessionEntryRow.tenant_id == job.tenant_id,
                        SdkSessionEntryRow.session_id.in_(session_ids),
                    )
                )
            ).all()
            count = len(rows)
            await db.execute(
                delete(SdkSessionEntryRow).where(
                    SdkSessionEntryRow.tenant_id == job.tenant_id,
                    SdkSessionEntryRow.session_id.in_(session_ids),
                )
            )
            await db.commit()
            return count


class MemoryLifecycleAdapter:
    name = "memory"

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def _rows(self, job: DataLifecycleJob) -> list[UserMemoryRow]:
        filters = [UserMemoryRow.tenant_id == job.tenant_id]
        if job.scope.kind is LifecycleScopeKind.USER:
            filters.append(UserMemoryRow.user_id == job.scope.subject_id)
        elif job.scope.kind is LifecycleScopeKind.AGENT:
            filters.append(UserMemoryRow.agent_name == job.scope.subject_id)
        elif job.scope.kind is LifecycleScopeKind.SESSION:
            return []
        async with self._sessions() as db:
            return list((await db.scalars(select(UserMemoryRow).where(*filters))).all())

    async def export(self, job: DataLifecycleJob) -> tuple[object, int]:
        rows = await self._rows(job)
        managed, consents, retentions = await self._managed(job)
        count = len(rows) + len(managed) + len(consents) + len(retentions)
        return {
            "memories": [row.payload for row in rows],
            "memoryBank": {
                "entries": [row.payload for row in managed],
                "consents": [row.payload for row in consents],
                "retentions": [row.payload for row in retentions],
            },
        }, count

    async def delete(self, job: DataLifecycleJob) -> int:
        rows = await self._rows(job)
        managed, consents, retentions = await self._managed(job)
        keys = [(row.user_id, row.agent_name) for row in rows]
        async with self._sessions() as db:
            for user_id, agent_name in keys:
                await db.execute(
                    delete(UserMemoryRow).where(
                        UserMemoryRow.tenant_id == job.tenant_id,
                        UserMemoryRow.user_id == user_id,
                        UserMemoryRow.agent_name == agent_name,
                    )
                )
            job_filter = [MemoryExtractionJobRow.tenant_id == job.tenant_id]
            if job.scope.kind is LifecycleScopeKind.USER:
                job_filter.append(MemoryExtractionJobRow.user_id == job.scope.subject_id)
            elif job.scope.kind is LifecycleScopeKind.AGENT:
                job_filter.append(MemoryExtractionJobRow.agent_name == job.scope.subject_id)
            elif job.scope.kind is LifecycleScopeKind.SESSION:
                job_filter.append(MemoryExtractionJobRow.session_id == job.scope.subject_id)
            # Keep cancelled tombstones so reconciliation cannot re-extract old runs.
            await db.execute(update(MemoryExtractionJobRow).where(*job_filter).values(
                status="cancelled", error_code="source_removed"))
            for row in managed:
                await db.delete(row)
            for row in (*consents, *retentions):
                await db.delete(row)
            await db.commit()
        return len(keys) + len(managed) + len(consents) + len(retentions)

    async def _managed(
        self, job: DataLifecycleJob
    ) -> tuple[list[MemoryEntryRow], list[MemoryConsentRow], list[MemoryRetentionRow]]:
        entry_filters = [MemoryEntryRow.tenant_id == job.tenant_id]
        policy_subject: str | None = None
        if job.scope.kind is LifecycleScopeKind.USER:
            entry_filters.append(MemoryEntryRow.user_id == job.scope.subject_id)
            policy_subject = job.scope.subject_id
        elif job.scope.kind is LifecycleScopeKind.AGENT:
            entry_filters.append(MemoryEntryRow.agent_name == job.scope.subject_id)
            policy_subject = job.scope.subject_id
        async with self._sessions() as db:
            entries = list(
                (await db.scalars(select(MemoryEntryRow).where(*entry_filters))).all()
            )
            if job.scope.kind is LifecycleScopeKind.SESSION:
                entries = [
                    row
                    for row in entries
                    if MemoryEntry.model_validate(row.payload).source.session_id
                    == job.scope.subject_id
                ]
                return entries, [], []
            consent_statement = select(MemoryConsentRow).where(
                MemoryConsentRow.tenant_id == job.tenant_id
            )
            retention_statement = select(MemoryRetentionRow).where(
                MemoryRetentionRow.tenant_id == job.tenant_id
            )
            if policy_subject is not None:
                if job.scope.kind is LifecycleScopeKind.USER:
                    consent_statement = consent_statement.where(
                        MemoryConsentRow.user_id == policy_subject
                    )
                    retention_statement = retention_statement.where(
                        MemoryRetentionRow.user_id == policy_subject
                    )
                else:
                    consent_statement = consent_statement.where(
                        MemoryConsentRow.agent_name == policy_subject
                    )
                    retention_statement = retention_statement.where(
                        MemoryRetentionRow.agent_name == policy_subject
                    )
            consents = list((await db.scalars(consent_statement)).all())
            retentions = list((await db.scalars(retention_statement)).all())
        return entries, consents, retentions


class PostgresLifecycleAdapter:
    name = "postgresql"

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def export(self, job: DataLifecycleJob) -> tuple[object, int]:
        index = await lifecycle_index(self._sessions, job)
        run_ids = [item.run_id for item in index.runs]
        async with self._sessions() as db:
            events = (
                (
                    await db.scalars(
                        select(EventRow).where(
                            EventRow.tenant_id == job.tenant_id,
                            EventRow.run_id.in_(run_ids),
                        )
                    )
                ).all()
                if run_ids
                else []
            )
            approvals = (
                (
                    await db.scalars(
                        select(ApprovalRow).where(
                            ApprovalRow.tenant_id == job.tenant_id,
                            ApprovalRow.run_id.in_(run_ids),
                        )
                    )
                ).all()
                if run_ids
                else []
            )
            artifacts = (
                (
                    await db.scalars(
                        select(ArtifactRow).where(
                            ArtifactRow.tenant_id == job.tenant_id,
                            ArtifactRow.artifact_id.in_(index.artifact_ids),
                        )
                    )
                ).all()
                if index.artifact_ids
                else []
            )
            session_ids = [item.session_id for item in index.sessions]
            context_states = (
                (
                    await db.scalars(
                        select(SessionContextStateRow).where(
                            SessionContextStateRow.tenant_id == job.tenant_id,
                            SessionContextStateRow.session_id.in_(session_ids),
                        )
                    )
                ).all()
                if session_ids
                else []
            )
            context_digests = (
                (
                    await db.scalars(
                        select(SessionContextDigestRow).where(
                            SessionContextDigestRow.tenant_id == job.tenant_id,
                            SessionContextDigestRow.session_id.in_(session_ids),
                        )
                    )
                ).all()
                if session_ids
                else []
            )
            eval_runs = (
                (
                    await db.scalars(
                        select(EvalRunRow).where(
                            EvalRunRow.tenant_id == job.tenant_id,
                            EvalRunRow.eval_run_id.in_(index.eval_run_ids),
                        )
                    )
                ).all()
                if index.eval_run_ids
                else []
            )
            audits = await db.scalars(
                select(AuditLogRow).where(
                    AuditLogRow.tenant_id == job.tenant_id,
                    *(
                        [AuditLogRow.user_id == job.scope.subject_id]
                        if job.scope.kind is LifecycleScopeKind.USER
                        else []
                    ),
                )
            )
            audit_payloads = [
                {
                    "auditId": row.audit_id,
                    "occurredAt": row.occurred_at.isoformat(),
                    "userId": row.user_id,
                    "action": row.action,
                    "resourceType": row.resource_type,
                    "resourceId": row.resource_id,
                    "outcome": row.outcome,
                    "details": row.details,
                }
                for row in audits
            ]
        data = {
            "sessions": [item.model_dump(mode="json") for item in index.sessions],
            "runs": [item.model_dump(mode="json") for item in index.runs],
            "events": [row.payload for row in events],
            "approvals": [row.payload for row in approvals],
            "artifacts": [row.payload for row in artifacts],
            "contextStates": [row.payload for row in context_states],
            "contextDigests": [row.payload for row in context_digests],
            "evalRuns": [row.payload for row in eval_runs],
            "audit": audit_payloads,
        }
        return data, sum(len(value) for value in data.values())

    async def delete(self, job: DataLifecycleJob) -> int:
        index = await lifecycle_index(self._sessions, job)
        run_ids = [item.run_id for item in index.runs]
        session_ids = [item.session_id for item in index.sessions]
        count = 0
        async with self._sessions() as db:
            statements = []
            if run_ids:
                statements.extend(
                    [
                        delete(EventRow).where(
                            EventRow.tenant_id == job.tenant_id,
                            EventRow.run_id.in_(run_ids),
                        ),
                        delete(ApprovalRow).where(
                            ApprovalRow.tenant_id == job.tenant_id,
                            ApprovalRow.run_id.in_(run_ids),
                        ),
                        delete(ArtifactRow).where(
                            ArtifactRow.tenant_id == job.tenant_id,
                            ArtifactRow.run_id.in_(run_ids),
                        ),
                        delete(QualityScoreRow).where(
                            QualityScoreRow.tenant_id == job.tenant_id,
                            QualityScoreRow.run_id.in_(run_ids),
                        ),
                        delete(RunRow).where(
                            RunRow.tenant_id == job.tenant_id,
                            RunRow.run_id.in_(run_ids),
                        ),
                    ]
                )
            if session_ids:
                statements.extend(
                    [
                        delete(ThreadFileRow).where(
                            ThreadFileRow.tenant_id == job.tenant_id,
                            ThreadFileRow.session_id.in_(session_ids),
                        ),
                        delete(WorkspaceSnapshotRow).where(
                            WorkspaceSnapshotRow.tenant_id == job.tenant_id,
                            WorkspaceSnapshotRow.session_id.in_(session_ids),
                        ),
                        delete(AguiThreadBindingRow).where(
                            AguiThreadBindingRow.tenant_id == job.tenant_id,
                            AguiThreadBindingRow.session_id.in_(session_ids),
                        ),
                        delete(SessionContextDigestRow).where(
                            SessionContextDigestRow.tenant_id == job.tenant_id,
                            SessionContextDigestRow.session_id.in_(session_ids),
                        ),
                        delete(SessionContextStateRow).where(
                            SessionContextStateRow.tenant_id == job.tenant_id,
                            SessionContextStateRow.session_id.in_(session_ids),
                        ),
                        delete(SessionRow).where(
                            SessionRow.tenant_id == job.tenant_id,
                            SessionRow.session_id.in_(session_ids),
                        ),
                    ]
                )
            if index.input_artifact_ids:
                statements.append(
                    delete(InputArtifactRow).where(
                        InputArtifactRow.tenant_id == job.tenant_id,
                        InputArtifactRow.input_artifact_id.in_(index.input_artifact_ids),
                    )
                )
            if index.eval_run_ids:
                statements.extend(
                    [
                        delete(EvalCaseResultRow).where(
                            EvalCaseResultRow.tenant_id == job.tenant_id,
                            EvalCaseResultRow.eval_run_id.in_(index.eval_run_ids),
                        ),
                        delete(EvalRunRow).where(
                            EvalRunRow.tenant_id == job.tenant_id,
                            EvalRunRow.eval_run_id.in_(index.eval_run_ids),
                        ),
                    ]
                )
            if index.eval_dataset_ids:
                statements.append(
                    delete(EvalDatasetVersionRow).where(
                        EvalDatasetVersionRow.tenant_id == job.tenant_id,
                        EvalDatasetVersionRow.dataset_id.in_(index.eval_dataset_ids),
                    )
                )
            if job.kind is not LifecycleJobKind.RETENTION and job.scope.kind in {
                LifecycleScopeKind.TENANT,
                LifecycleScopeKind.AGENT,
            }:
                agent_filter = (
                    [QualityRuleRow.agent_name == job.scope.subject_id]
                    if job.scope.kind is LifecycleScopeKind.AGENT
                    else []
                )
                statements.extend(
                    [
                        delete(QualityRuleRow).where(
                            QualityRuleRow.tenant_id == job.tenant_id,
                            *agent_filter,
                        ),
                        delete(QualityIncidentRow).where(
                            QualityIncidentRow.tenant_id == job.tenant_id,
                            *(
                                [QualityIncidentRow.agent_name == job.scope.subject_id]
                                if job.scope.kind is LifecycleScopeKind.AGENT
                                else []
                            ),
                        ),
                    ]
                )
            for statement in statements:
                result = await db.execute(statement)
                count += int(getattr(result, "rowcount", 0) or 0)
            await db.commit()
        return count


class ExternalDeletionPendingError(RuntimeError):
    """Langfuse accepted deletion but still returns the trace."""


class LangfuseLifecycleAdapter:
    name = "langfuse"

    def __init__(
        self,
        sessions: SessionFactory,
        *,
        base_url: str,
        public_key: str,
        secret_key: SecretStr,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10,
    ) -> None:
        self._sessions = sessions
        self._base_url = base_url.rstrip("/")
        self._auth = httpx.BasicAuth(public_key, secret_key.get_secret_value())
        self._transport = transport
        self._timeout = timeout

    async def export(self, job: DataLifecycleJob) -> tuple[object, int]:
        index = await lifecycle_index(self._sessions, job)
        traces: list[object] = []
        async with httpx.AsyncClient(
            auth=self._auth, timeout=self._timeout, transport=self._transport
        ) as client:
            for trace_id in index.trace_ids:
                response = await client.get(f"{self._base_url}/api/public/traces/{trace_id}")
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                traces.append(response.json())
        return {"traces": traces}, len(traces)

    async def delete(self, job: DataLifecycleJob) -> int:
        index = await lifecycle_index(self._sessions, job)
        if not index.trace_ids:
            return 0
        pending: list[str] = []
        async with httpx.AsyncClient(
            auth=self._auth, timeout=self._timeout, transport=self._transport
        ) as client:
            for trace_id in index.trace_ids:
                response = await client.delete(f"{self._base_url}/api/public/traces/{trace_id}")
                if response.status_code not in {200, 202, 204, 404}:
                    response.raise_for_status()
                verify = await client.get(f"{self._base_url}/api/public/traces/{trace_id}")
                if verify.status_code != 404:
                    pending.append(trace_id)
        if pending:
            raise ExternalDeletionPendingError(
                f"Langfuse deletion is still pending for {len(pending)} trace(s)"
            )
        return len(index.trace_ids)
