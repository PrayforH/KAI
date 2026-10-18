from datetime import UTC, datetime

import jwt
import pytest
from pydantic import SecretStr

from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import ExecutionIdentity, Run, RunStatus
from harness.memory_bank.repositories import InMemoryMemoryBankRepository, dedup_key
from harness.memory_bank.service import MemoryBankService
from harness.memory_bank.workload import MemoryWorkloadTokenService

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def identity(project: str | None) -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant",
        user_id="user",
        project_id="analyst",
        business_project_id=project,
        agent_name="analyst",
        agent_version="1",
        session_id="session",
        run_id="run",
    )


@pytest.mark.asyncio
async def test_project_recall_read_dedup_and_corrections_do_not_cross_scope():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    entries = []
    for project in (None, "project-a", "project-b"):
        entry = await bank.propose_agent(identity(project), "季度经营分析采用含税收入")
        entries.append(await bank.confirm("tenant", "user", entry.entry_id, entry.version))
    assert len({dedup_key(entry) for entry in entries}) == 3
    for project, entry in zip((None, "project-a", "project-b"), entries, strict=True):
        hits = await bank.search("tenant", "user", "analyst", "季度经营分析", project_id=project)
        assert [hit.entry.entry_id for hit in hits] == [entry.entry_id]
        assert entry.entry_id in await bank.projection(identity(project), query="季度经营分析")
        assert await bank.read(identity(project), entry.entry_id) == entry
        for foreign in (item for item in entries if item.entry_id != entry.entry_id):
            with pytest.raises(NotFoundError):
                await bank.read(identity(project), foreign.entry_id)
            with pytest.raises(ConflictError, match="outside"):
                await bank.propose_agent(
                    identity(project),
                    "改用不含税收入",
                    supersedes=foreign.entry_id,
                    supersedes_version=foreign.version,
                )
    assert len(await bank.list_entries("tenant", "user")) == 3
    assert len((await bank.export_user("tenant", "user"))["entries"]) == 3


@pytest.mark.asyncio
async def test_semantic_ranking_filters_project_before_the_limit():
    repo = InMemoryMemoryBankRepository()
    bank = MemoryBankService(repo, clock=lambda: NOW)
    for project in (None, "a", "b"):
        entry = await bank.propose_agent(identity(project), f"{project} 的经营分析资料")
        entry = await bank.confirm("tenant", "user", entry.entry_id, entry.version)
        await repo.put_embedding(entry, "embedding", [1.0, 0.0])
    hits = await repo.semantic_search(
        "tenant", "user", "analyst", "user", "embedding", [1.0, 0.0], NOW, limit=1, project_id="b"
    )
    assert len(hits) == 1 and hits[0].entry.project_id == "b"


def test_workload_tokens_keep_legacy_capability_scope_and_add_business_scope():
    secret = SecretStr("project-scope-token-test-secret-123456789")
    tokens = MemoryWorkloadTokenService(secret)
    current = identity("a")
    assert tokens.verify(tokens.issue(current)) == current
    payload = jwt.decode(
        tokens.issue(current),
        secret.get_secret_value(),
        algorithms=["HS256"],
        audience="harness-memory-bank",
    )
    assert payload.pop("business_project") == "a"
    payload.pop("scope_version")
    legacy = jwt.encode(payload, secret.get_secret_value(), algorithm="HS256")
    assert tokens.verify(legacy) == identity(None)


@pytest.mark.parametrize(
    "input_data", [{"business_project_id": "a"}, {"project_context_meta": {"projectId": "a"}}, {}]
)
def test_run_scope_comes_from_its_snapshot_not_current_thread_assignment(input_data):
    run = Run(
        run_id="run",
        session_id="session",
        tenant_id="tenant",
        status=RunStatus.SUCCEEDED,
        idempotency_key="key",
        created_at=NOW,
        updated_at=NOW,
        input=input_data,
    )
    assert run.business_project_id == ("a" if input_data else None)
