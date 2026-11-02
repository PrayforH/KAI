"""The remote memory MCP carries the same specialist scope as extraction and projection.

A duty thread's `propose_memory` travels to the HTTP memory MCP inside a workload
token built from the run identity. If that token keeps the underlying agent name,
a Bot conversation writes its domain memory under the shared agent scope where the
specialist read side (`specialist:{slug}`) can never see it. The runtime must
resolve the session scope before issuing the token.
"""

# pyright: reportPrivateUsage=false

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from harness.core.manifest import load_manifest
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    ExecutionIdentity,
    ModelCompatibility,
    ModelRoute,
    Run,
    RunStatus,
    Session,
)
from harness.memory_bank.repositories import InMemoryMemoryBankRepository
from harness.memory_bank.service import MemoryBankService
from harness.runtime.base import RuntimeContext
from harness.runtime.claude_sdk import ClaudeSdkRuntime
from harness.runtime.tools import ResolvedTools

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="new-api-default",
        provider="new-api",
        base_url="https://new-api.example/v1",
        model="gateway-model",
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use"}),
    )


def _runtime(bank: MemoryBankService, recorder: "RecordingMemoryMcp") -> ClaudeSdkRuntime:
    snapshot = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    version = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="echo-agent",
        version="0.1.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=NOW,
    )
    return ClaudeSdkRuntime(
        agent_version=version,
        routes=[_route()],
        route_secrets={"new-api-default": "secret"},
        memory_bank=bank,
        remote_memory_mcp=cast("Any", recorder),
    )


class RecordingMemoryMcp:
    """Records the identity each attach would encode into its workload token."""

    def __init__(self) -> None:
        self.identities: list[ExecutionIdentity] = []

    @property
    def enabled(self) -> bool:
        return True

    def attach(self, tools: ResolvedTools, identity: ExecutionIdentity) -> ResolvedTools:
        self.identities.append(identity)
        return tools


def _context(tmp_path, session_id: str) -> RuntimeContext:
    return RuntimeContext(
        run=Run(
            run_id="run-memory",
            session_id=session_id,
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="memory-scope",
            created_at=NOW,
            updated_at=NOW,
            input={"prompt": "记住交付目录"},
        ),
        session=Session(
            session_id=session_id,
            tenant_id="tenant-a",
            user_id="user-a",
            agent_name="archive-assistant-agent",
            agent_version="0.1.1",
            created_at=NOW,
        ),
        workspace=tmp_path,
        identity=ExecutionIdentity(
            tenant_id="tenant-a",
            user_id="user-a",
            project_id="archive-assistant-agent",
            session_id=session_id,
            run_id="run-memory",
            agent_name="archive-assistant-agent",
            agent_version="0.1.1",
        ),
        runtime_transport_factory=lambda options: None,
    )


@pytest.mark.asyncio
async def test_a_duty_session_issues_a_specialist_scoped_memory_token(
    tmp_path,
) -> None:
    bank_repo = InMemoryMemoryBankRepository()
    bank_repo.session_scopes["session-duty"] = "specialist:cost-monitor"
    recorder = RecordingMemoryMcp()
    runtime = _runtime(MemoryBankService(bank_repo, clock=lambda: NOW), recorder)

    _options, _tools = await runtime._options(
        _context(tmp_path, "session-duty"), _route()
    )

    assert [identity.agent_name for identity in recorder.identities] == [
        "specialist:cost-monitor"
    ]


@pytest.mark.asyncio
async def test_a_plain_session_keeps_the_agent_scope_for_memory(tmp_path) -> None:
    recorder = RecordingMemoryMcp()
    runtime = _runtime(
        MemoryBankService(InMemoryMemoryBankRepository(), clock=lambda: NOW), recorder
    )

    _options, _tools = await runtime._options(
        _context(tmp_path, "session-plain"), _route()
    )

    assert [identity.agent_name for identity in recorder.identities] == [
        "archive-assistant-agent"
    ]
