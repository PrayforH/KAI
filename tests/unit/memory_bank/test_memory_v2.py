import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr

from harness.adapters.memory import InMemoryUserMemoryRepository
from harness.application.memory import UserMemoryService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import ExecutionIdentity
from harness.memory_bank.embedding import OpenAIEmbeddingClient
from harness.memory_bank.extraction import MemoryExtractor
from harness.memory_bank.models import MemorySourceKind, MemoryStatus, MemoryType
from harness.memory_bank.repositories import InMemoryMemoryBankRepository
from harness.memory_bank.service import MemoryBankService

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def identity(owner: str = "u") -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="t",
        user_id="u",
        agent_name="a",
        agent_version="1",
        agent_owner_user_id=owner,
        project_id="p",
        session_id="s",
        run_id="r",
    )


async def active(bank: MemoryBankService, content: str, owner: str = "u", **kwargs):
    candidate = await bank.propose_agent(identity(owner), content, **kwargs)
    return await bank.confirm("t", "u", candidate.entry_id, candidate.version)


@pytest.mark.asyncio
async def test_old_relevant_memory_after_1000_items_is_retrieved_and_projected():
    repo = InMemoryMemoryBankRepository()
    bank = MemoryBankService(repo, clock=lambda: NOW)
    original = await active(bank, "报告需要提供原始出处")
    for i in range(1005):
        e = original.model_copy(
            update={
                "entry_id": f"other{i}",
                "content": f"其他主题{i}",
                "content_hash": str(i),
                "updated_at": NOW + timedelta(seconds=i + 1),
            }
        )
        await repo.add_entry(e)
    assert (await bank.search("t", "u", "a", "原始出处"))[0].entry.entry_id == original.entry_id
    projection = await bank.projection(identity(), query="原始出处")
    assert "报告需要提供原始出处" in projection
    assert len(projection) <= 4000 and projection.endswith("</memory_bank>")


@pytest.mark.asyncio
async def test_budget_complete_entries_and_uniform_proposal_limit():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    e = await active(bank, "长内容" * 1300)
    rendered = await bank.projection(identity())
    assert len(rendered) <= 4000 and rendered.endswith("</memory_bank>")
    assert "read_memory" in rendered and e.entry_id in rendered
    assert (await bank.read(identity(), e.entry_id)).content == e.content
    with pytest.raises(ConflictError):
        await bank.propose_agent(identity(), "长" * 4001)


@pytest.mark.asyncio
async def test_owner_isolation_and_concurrent_exact_dedup():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    first, second = await asyncio.gather(
        bank.propose_agent(identity(), "固定中文"), bank.propose_agent(identity(), "固定中文")
    )
    assert first.entry_id == second.entry_id
    e = await active(bank, "别人偏好中文", owner="another-owner")
    assert await bank.search("t", "u", "a", "别人偏好") == ()
    with pytest.raises(NotFoundError):
        await bank.read(identity(), e.entry_id)


@pytest.mark.asyncio
async def test_replacement_needs_confirmation_then_atomically_supersedes():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    await bank.replace_consent("t", "u", "a", expected_version=0, allow_agent_personal=True)
    old = await bank.propose_agent(identity(), "周报在周五提交")
    candidate = await bank.propose(
        tenant_id="t",
        user_id="u",
        agent_name="a",
        content="周报改为周四提交",
        source_kind=MemorySourceKind.AGENT,
        source_label="纠正",
        confidence=0.7,
        supersedes=old.entry_id,
        supersedes_version=old.version,
        evidence="以后周四交",
        memory_type=MemoryType.PREFERENCE,
    )
    assert candidate.status is MemoryStatus.PENDING
    assert (await bank.read(identity(), old.entry_id)).content == old.content
    confirmed = await bank.confirm("t", "u", candidate.entry_id, candidate.version)
    assert confirmed.status is MemoryStatus.ACTIVE
    with pytest.raises(NotFoundError):
        await bank.read(identity(), old.entry_id)
    removed = await bank.delete("t", "u", confirmed.entry_id, confirmed.version)
    assert removed.source.evidence == "" and removed.content == "[DELETED]"


@pytest.mark.asyncio
async def test_stale_replacement_does_not_overwrite_user_edit():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    old = await active(bank, "周报在周五提交")
    candidate = await bank.propose(
        tenant_id="t",
        user_id="u",
        agent_name="a",
        content="周报在周四提交",
        source_kind=MemorySourceKind.AGENT,
        source_label="纠正",
        confidence=0.7,
        supersedes=old.entry_id,
        supersedes_version=old.version,
    )
    await bank.update(
        "t",
        "u",
        old.entry_id,
        expected_version=old.version,
        content="周报在周三提交",
        confidence=None,
    )
    with pytest.raises(ConflictError):
        await bank.confirm("t", "u", candidate.entry_id, candidate.version)
    assert (await bank.read(identity(), old.entry_id)).content == "周报在周三提交"


@pytest.mark.asyncio
async def test_legacy_import_does_not_fill_prompt_or_resurrect():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    legacy = UserMemoryService(InMemoryUserMemoryRepository(), clock=lambda: NOW, memory_bank=bank)
    await legacy.update(identity(), "旧内容" * 1300)
    assert await legacy.projection(identity(), "新问题") == ""
    entries = await bank.list_entries("t", "u")
    assert len(entries) == 1 and entries[0].status is MemoryStatus.PENDING
    await bank.reject("t", "u", entries[0].entry_id, 1)
    await legacy.projection(identity(), "新问题")
    assert await bank.list_entries("t", "u") == ()


@pytest.mark.asyncio
async def test_embedding_response_validation_and_indexes():
    async def handler(request):
        assert json.loads(request.content)["model"] == "bge"
        return httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]},
        )

    client = OpenAIEmbeddingClient(
        "http://test/v1",
        SecretStr("test"),
        "bge",
        dimensions=2,
        transport=httpx.MockTransport(handler),
    )
    assert await client.embed(["一", "二"]) == ((1.0, 0.0), (0.0, 1.0))
    with pytest.raises(ValueError):
        await client.embed(["一"])


@pytest.mark.asyncio
async def test_extraction_requires_evidence_and_known_replacement():
    async def handler(_request):
        candidates = [
            {"content": "用户偏好中文", "evidence": "我习惯使用中文", "memory_type": "preference"},
            {"content": "编造", "evidence": "不存在的原文"},
            {"content": "替代", "evidence": "我习惯使用中文", "supersedes": "another-scope"},
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"candidates": candidates}, ensure_ascii=False)
                        }
                    }
                ]
            },
        )

    extractor = MemoryExtractor(
        "http://test/v1", SecretStr("test"), "chat", transport=httpx.MockTransport(handler)
    )
    candidates = await extractor.extract("我习惯使用中文", [])
    assert len(candidates) == 1 and candidates[0].memory_type is MemoryType.PREFERENCE


@pytest.mark.asyncio
async def test_expired_duplicate_can_be_proposed_again_and_edits_do_not_copy_evidence():
    now = NOW
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: now)
    first = await active(bank, "长期偏好中文")
    now = NOW + timedelta(days=181)
    second = await bank.propose_agent(identity(), "长期偏好中文")
    assert second.entry_id != first.entry_id
    assert (
        await bank.repository.get_entry("t", "u", first.entry_id)
    ).status is MemoryStatus.EXPIRED
    another = await bank.propose_agent(identity(), "长期偏好简洁")
    with pytest.raises(ConflictError):
        await bank.update(
            "t", "u", another.entry_id, expected_version=1, content=second.content, confidence=None
        )


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_and_durable_backlog_can_reindex():
    class OutageEmbedder:
        model = "test"
        available = False

        async def embed(self, texts):
            if not self.available:
                raise TimeoutError("provider unavailable")
            return [[1.0] + [0.0] * 1023 for _ in texts]

    embedder = OutageEmbedder()
    repo = InMemoryMemoryBankRepository()
    bank = MemoryBankService(repo, clock=lambda: NOW, embedder=embedder)
    entry = await active(bank, "月报应注明原始出处")
    assert (await bank.search("t", "u", "a", "原始出处"))[0].entry.entry_id == entry.entry_id
    assert len(await repo.unindexed("test", NOW)) == 1
    embedder.available = True
    assert await bank.reindex_pending() == 1
    assert not await repo.unindexed("test", NOW)


@pytest.mark.asyncio
async def test_extraction_enriches_same_turn_proposal_with_correction_link():
    bank = MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW)
    old = await active(bank, "周报在周五交付")
    plain = await bank.propose_agent(identity(), "周报在周四交付")
    linked = await bank.propose(
        tenant_id="t",
        user_id="u",
        agent_name="a",
        content=plain.content,
        source_kind=MemorySourceKind.AGENT,
        source_label="提取器",
        confidence=0.7,
        run_id="r",
        supersedes=old.entry_id,
        supersedes_version=old.version,
        evidence="以后周报改为周四交付",
    )
    assert linked.entry_id == plain.entry_id and linked.version == plain.version + 1
    assert linked.supersedes == old.entry_id
    confirmed = await bank.confirm("t", "u", linked.entry_id, linked.version)
    assert confirmed.status is MemoryStatus.ACTIVE
    with pytest.raises(NotFoundError):
        await bank.read(identity(), old.entry_id)


def test_embedding_dimensions_parse_from_environment(monkeypatch):
    from harness.config import Settings
    monkeypatch.setenv("HARNESS_MEMORY_EMBEDDING_DIMENSIONS", "1024")
    assert Settings(_env_file=None).memory_embedding_dimensions == 1024
