from datetime import UTC, datetime

import pytest

from harness.core.models import ExecutionIdentity
from harness.memory_bank.models import MemoryStatus
from harness.memory_bank.repositories import InMemoryMemoryBankRepository
from harness.memory_bank.scope import policy_key
from harness.memory_bank.service import MemoryBankService


def identity(owner: str = "user-a") -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a", user_id="user-a", project_id="agent-a",
        session_id="session-a", run_id="run-a", agent_name="agent-a",
        agent_version="1.0.0", agent_owner_user_id=owner,
    )


def bank(enabled: bool = True) -> MemoryBankService:
    return MemoryBankService(
        InMemoryMemoryBankRepository(),
        clock=lambda: datetime(2026, 10, 13, tzinfo=UTC),
        default_allow_agent_personal=enabled,
    )


@pytest.mark.asyncio
async def test_default_on_saves_general_memory_but_sensitive_memory_stays_pending():
    subject = bank()
    ordinary = await subject.propose_agent(identity(), "演示项目汇报先给结论，再列风险")
    sensitive = await subject.propose_agent(identity(), "测试邮箱 approval-demo@example.invalid")
    assert ordinary.status is MemoryStatus.ACTIVE
    assert ordinary.consent_id is not None
    assert sensitive.status is MemoryStatus.PENDING
    assert sensitive.consent_id is None
    assert sensitive.content not in await subject.projection(identity())


@pytest.mark.asyncio
async def test_initialized_default_policy_can_be_explicitly_disabled():
    subject = bank()
    policy, _ = await subject.get_policy("tenant-a", "user-a", "agent-a")
    assert policy and policy.allow_agent_personal and policy.version == 1
    await subject.replace_consent(
        "tenant-a", "user-a", "agent-a",
        expected_version=policy.version, allow_agent_personal=False,
    )
    proposed = await subject.propose_agent(identity(), "演示项目使用简洁答复")
    assert proposed.status is MemoryStatus.PENDING
    policy, _ = await subject.get_policy("tenant-a", "user-a", "agent-a")
    assert policy and not policy.allow_agent_personal and policy.version == 2


@pytest.mark.asyncio
async def test_default_off_does_not_create_consent_or_activate_memory():
    subject = bank(False)
    policy, _ = await subject.get_policy("tenant-a", "user-a", "agent-a")
    assert policy is None
    assert (await subject.propose_agent(identity(), "使用中文答复")).status is MemoryStatus.PENDING


@pytest.mark.asyncio
async def test_default_does_not_cross_agent_owners_and_corrections_require_confirmation():
    subject = bank()
    own_key = policy_key("user-a", "agent-a", "user-a")
    other_key = policy_key("user-a", "agent-a", "owner-b")
    await subject.replace_consent(
        "tenant-a", "user-a", own_key, expected_version=0, allow_agent_personal=False,
    )
    original = await subject.propose_agent(identity("owner-b"), "先给结论，再列风险")
    assert original.status is MemoryStatus.ACTIVE
    correction = await subject.propose_agent(
        identity("owner-b"), "先列风险，再给结论",
        supersedes=original.entry_id, supersedes_version=original.version,
    )
    assert correction.status is MemoryStatus.PENDING
    own, _ = await subject.get_policy("tenant-a", "user-a", own_key)
    other, _ = await subject.get_policy("tenant-a", "user-a", other_key)
    assert own and not own.allow_agent_personal
    assert other and other.allow_agent_personal
