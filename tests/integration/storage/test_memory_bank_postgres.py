import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from harness.core.errors import NotFoundError
from harness.core.models import ExecutionIdentity
from harness.memory_bank.models import MemoryStatus
from harness.memory_bank.service import MemoryBankService
from harness.storage.database import SessionFactory, create_database, create_schema, drop_schema
from harness.storage.memory_bank_repository import PostgresMemoryBankRepository

NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest_asyncio.fixture
async def memory_database() -> AsyncIterator[SessionFactory]:
    engine, sessions = create_database(DATABASE_URL)
    await drop_schema(engine)
    await create_schema(engine)
    try:
        yield sessions
    finally:
        await engine.dispose()


def identity(user: str = "user-a") -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a",
        user_id=user,
        project_id="agent-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="agent-a",
        agent_version="1.0.0",
    )


@pytest.mark.asyncio
async def test_memory_bank_is_durable_scoped_and_fenced(
    memory_database: SessionFactory,
) -> None:
    first = MemoryBankService(PostgresMemoryBankRepository(memory_database), clock=lambda: NOW)
    proposal = await first.propose_agent(identity(), "用户偏好中文月报")
    confirmed = await first.confirm("tenant-a", "user-a", proposal.entry_id, proposal.version)

    restarted = MemoryBankService(PostgresMemoryBankRepository(memory_database), clock=lambda: NOW)
    hits = await restarted.search("tenant-a", "user-a", "agent-a", "中文月报")
    assert len(hits) == 1 and hits[0].entry.status is MemoryStatus.ACTIVE
    with pytest.raises(NotFoundError):
        await restarted.repository.get_entry("tenant-a", "user-b", proposal.entry_id)

    edited = await restarted.update(
        "tenant-a",
        "user-a",
        proposal.entry_id,
        expected_version=confirmed.version,
        content="用户偏好中文周报",
        confidence=0.9,
    )
    stale = confirmed.model_copy(update={"content": "stale", "version": confirmed.version + 1})
    assert not await restarted.repository.compare_and_set_entry(confirmed.version, stale)
    assert edited.version == 3


@pytest.mark.asyncio
async def test_consent_policy_is_durable_and_never_auto_accepts_sensitive_memory(
    memory_database: SessionFactory,
) -> None:
    service = MemoryBankService(PostgresMemoryBankRepository(memory_database), clock=lambda: NOW)
    await service.replace_consent(
        "tenant-a",
        "user-a",
        "agent-a",
        expected_version=0,
        allow_agent_personal=True,
    )

    personal = await service.propose_agent(identity(), "用户喜欢简洁回答")
    sensitive = await service.propose_agent(identity(), "用户病历记录有花粉过敏")

    assert personal.status is MemoryStatus.ACTIVE
    assert sensitive.status is MemoryStatus.PENDING


@pytest.mark.asyncio
async def test_pgvector_scopes_versions_and_delete(memory_database: SessionFactory) -> None:
    repository = PostgresMemoryBankRepository(memory_database)
    bank = MemoryBankService(repository, clock=lambda: NOW)
    e = await bank.propose_agent(identity(), "用户偏好短回答")
    e = await bank.confirm("tenant-a", "user-a", e.entry_id, e.version)
    vector = [1.0] + [0.0] * 1023
    assert await repository.put_embedding(e, "bge", vector)
    hits = await repository.semantic_search(
        "tenant-a", "user-a", "agent-a", "user-a", "bge", vector, NOW, limit=8
    )
    assert len(hits) == 1 and hits[0].score > 0.99
    assert not await repository.semantic_search(
        "tenant-a", "user-a", "agent-a", "other-owner", "bge", vector, NOW, limit=8
    )
    changed = await bank.update(
        "tenant-a",
        "user-a",
        e.entry_id,
        expected_version=e.version,
        content="用户希望详细回答",
        confidence=None,
    )
    assert not await repository.put_embedding(e, "bge", vector)
    assert not await repository.semantic_search(
        "tenant-a", "user-a", "agent-a", "user-a", "bge", vector, NOW, limit=8
    )
    assert await repository.put_embedding(changed, "bge", vector)
    await bank.delete("tenant-a", "user-a", changed.entry_id, changed.version)
    assert not await repository.put_embedding(changed, "bge", vector)
    assert not await repository.semantic_search(
        "tenant-a", "user-a", "agent-a", "user-a", "bge", vector, NOW, limit=8
    )


@pytest.mark.asyncio
async def test_pg_concurrent_proposal_and_atomic_replacement(
    memory_database: SessionFactory,
) -> None:
    import asyncio
    from harness.memory_bank.models import MemorySourceKind

    bank = MemoryBankService(PostgresMemoryBankRepository(memory_database), clock=lambda: NOW)
    proposals = await asyncio.gather(
        *(bank.propose_agent(identity(), "周五提交周报") for _ in range(5))
    )
    assert len({e.entry_id for e in proposals}) == 1
    old = await bank.confirm("tenant-a", "user-a", proposals[0].entry_id, 1)
    replacement = await bank.propose(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        content="周四提交周报",
        source_kind=MemorySourceKind.AGENT,
        source_label="纠正",
        confidence=0.7,
        supersedes=old.entry_id,
        supersedes_version=old.version,
    )
    await bank.confirm("tenant-a", "user-a", replacement.entry_id, 1)
    assert (
        await bank.repository.get_entry("tenant-a", "user-a", old.entry_id)
    ).status is MemoryStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_extraction_job_reconciliation_evidence_and_idempotency(
    memory_database: SessionFactory,
) -> None:
    import json
    import httpx
    from pydantic import SecretStr
    from sqlalchemy import select
    from harness.core.models import Run, RunStatus, Session
    from harness.memory_bank.extraction import MemoryExtractor
    from harness.memory_bank.processing import MemoryProcessingController
    from harness.storage.models import RunRow, SessionRow, MemoryExtractionJobRow

    run = Run(
        tenant_id="tenant-a",
        run_id="run-a",
        session_id="session-a",
        status=RunStatus.SUCCEEDED,
        idempotency_key="test",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "以后正式材料使用中文，先给结论"},
    )
    session = Session(
        tenant_id="tenant-a",
        session_id="session-a",
        user_id="user-a",
        agent_name="agent-a",
        agent_version="1",
        created_at=NOW,
    )
    async with memory_database() as db:
        db.add(
            SessionRow(
                tenant_id=session.tenant_id,
                session_id=session.session_id,
                user_id=session.user_id,
                payload=session.model_dump(mode="json"),
            )
        )
        db.add(
            RunRow(
                tenant_id=run.tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                idempotency_key="test",
                status="succeeded",
                fencing_token=0,
                updated_at=NOW,
                payload=run.model_dump(mode="json"),
            )
        )
        await db.commit()
    calls = []

    async def handler(request):
        calls.append(json.loads(request.content))
        candidate = {
            "content": "正式材料使用中文，结论在前",
            "memory_type": "preference",
            "topic": "正式材料",
            "evidence": "以后正式材料使用中文，先给结论",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"candidates": [candidate]}, ensure_ascii=False)
                        }
                    }
                ]
            },
        )

    extractor = MemoryExtractor(
        "http://test/v1", SecretStr("test"), "chat", transport=httpx.MockTransport(handler)
    )
    bank = MemoryBankService(PostgresMemoryBankRepository(memory_database))
    controller = MemoryProcessingController(memory_database, bank, extractor, since=NOW)
    assert await controller.process_once() == 1
    assert await controller.process_once() == 0
    assert len(calls) == 1
    entries = await bank.list_entries("tenant-a", "user-a")
    assert len(entries) == 1 and entries[0].status is MemoryStatus.PENDING
    assert entries[0].source.evidence == run.input["prompt"]
    await bank.reject("tenant-a", "user-a", entries[0].entry_id, 1)
    async with memory_database() as db:
        job = await db.scalar(select(MemoryExtractionJobRow))
        assert job is not None
        job.status = "retrying"
        job.available_at = NOW
        await db.commit()
    # Even a replay after user deletion is blocked by the durable removal boundary.
    assert await controller.process_once() == 1
    assert not await bank.list_entries("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_extraction_correction_enrichment_obeys_lease(
    memory_database: SessionFactory,
) -> None:
    from harness.core.errors import ConflictError
    from harness.memory_bank.models import MemorySourceKind
    from harness.storage.models import MemoryExtractionJobRow

    bank = MemoryBankService(PostgresMemoryBankRepository(memory_database), clock=lambda: NOW)
    old = await bank.propose_agent(identity(), "周报在周五交付")
    old = await bank.confirm("tenant-a", "user-a", old.entry_id, old.version)
    pending = await bank.propose_agent(identity(), "周报在周四交付")
    async with memory_database() as db:
        db.add(
            MemoryExtractionJobRow(
                tenant_id="tenant-a",
                run_id="run-a",
                user_id="user-a",
                agent_name="agent-a",
                agent_owner_user_id="user-a",
                session_id="session-a",
                status="cancelled",
                attempts=1,
                available_at=NOW,
                created_at=NOW,
            )
        )
        await db.commit()
    args = dict(
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        content=pending.content,
        source_kind=MemorySourceKind.AGENT,
        source_label="extractor",
        confidence=0.7,
        run_id="run-a",
        supersedes=old.entry_id,
        supersedes_version=old.version,
        extraction_job_id="run-a",
        extraction_version=1,
    )
    with pytest.raises(ConflictError):
        await bank.propose(**args)
    assert (await bank.repository.get_entry("tenant-a", "user-a", pending.entry_id)).version == 1
    async with memory_database() as db:
        job = await db.get(MemoryExtractionJobRow, ("tenant-a", "run-a"))
        assert job is not None
        job.status = "processing"
        await db.commit()
    linked = await bank.propose(**args)
    assert linked.entry_id == pending.entry_id and linked.supersedes == old.entry_id
    await bank.confirm("tenant-a", "user-a", linked.entry_id, linked.version)
    assert (
        await bank.repository.get_entry("tenant-a", "user-a", old.entry_id)
    ).status is MemoryStatus.SUPERSEDED
