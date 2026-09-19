"""The DeepAgents gate is the only thing between a model and a tool call.

DeepAgents has no hook bridge: ``awrap_tool_call`` is the whole authorization
surface. These tests pin the four things that make it trustworthy:

* it cannot be built without a policy source, and the source it is given is the
  one that actually authorizes — the wiring defect that made every DeepAgents Run
  fail on the first tool call is a build-time error here, not a runtime one;
* a tool name is translated into the platform vocabulary before any rule runs,
  so policies written against ``Bash``/``Write`` govern ``execute``/``write_file``;
* a denial becomes an error ``ToolMessage`` the model can read, never a crash,
  and it is written into the durable stream as ``tool.result``;
* the three narrow allow overrides fire only where they are meant to, so an
  operator's rule and the published tool ceiling still win.

The gate needs LangChain's middleware types but not the DeepAgents kernel itself,
so it is skipped on the narrower dependency.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip(
    "langchain.agents.middleware", reason="the DeepAgents extra provides the middleware types"
)

from langchain.agents.middleware.types import ToolCallRequest  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from harness.adapters.memory import (  # noqa: E402
    InMemoryApprovalRepository,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
)
from harness.application.approvals import ApprovalService  # noqa: E402
from harness.application.events import EventService  # noqa: E402
from harness.core.models import Run, RunStatus, Session  # noqa: E402
from harness.policy.models import PolicyDecision, PolicyRule  # noqa: E402
from harness.policy.profiles import default_policy_profiles  # noqa: E402
from harness.policy.results import ResultPolicyEngine  # noqa: E402
from harness.policy.rules import PolicyEngine, default_policy_rules  # noqa: E402
from harness.policy.runtime import ResolvedPolicy  # noqa: E402
from harness.quota.models import QuotaResource, ReplaceQuotaPolicyRequest  # noqa: E402
from harness.quota.repositories import InMemoryQuotaRepository  # noqa: E402
from harness.quota.service import QuotaService  # noqa: E402
from harness.runtime.base import RuntimeContext  # noqa: E402
from harness.runtime.deepagents_tool_gate import DeepagentsToolGate  # noqa: E402

NOW = datetime(2026, 9, 19, tzinfo=UTC)

# A declared MCP tool that no default rule mentions, so only the published tool
# directory can authorize it.
_DECLARED_MCP_TOOL = "mcp__tavily-readonly__tavily_search"
_BUNDLE_OPERATOR = "mcp__harness-python-score-agent__score"


def _ids() -> Callable[[str], str]:
    count = 0

    def generate(prefix: str) -> str:
        nonlocal count
        count += 1
        return f"{prefix}-{count}"

    return generate


async def _echo(request: ToolCallRequest) -> ToolMessage | Command[Any]:
    """Stand in for the tool the gate would let through."""

    return ToolMessage(
        content="ok",
        tool_call_id=str(request.tool_call.get("id") or ""),
        name=str(request.tool_call.get("name") or ""),
    )


async def _sandbox_executor(command: str, **kwargs: Any) -> Any:
    """Presence is the whole point: the gate only checks that one exists."""

    raise AssertionError(f"the stub executor must not be called: {command}")


async def _arrange(
    tmp_path: Path,
    *,
    profiles: bool = True,
    policy_rules: Sequence[PolicyRule] | None = None,
    resolved_policy: ResolvedPolicy | None = None,
    declared_tools: frozenset[str] = frozenset(),
    quotas: QuotaService | None = None,
    with_sandbox_executor: bool = False,
) -> tuple[DeepagentsToolGate, EventService, RuntimeContext]:
    runs = InMemoryRunRepository()
    events = EventService(
        InMemoryEventRepository(),
        InMemoryEventBus(),
        clock=lambda: NOW,
        id_generator=_ids(),
    )
    run = Run(
        run_id="run-deepagents",
        session_id="session-deepagents",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="deepagents-gate",
        created_at=NOW,
        updated_at=NOW,
    )
    await runs.add(run)
    approvals = ApprovalService(
        runs=runs,
        approvals=InMemoryApprovalRepository(),
        events=events,
        clock=lambda: NOW,
        id_generator=_ids(),
        ttl=timedelta(minutes=5),
    )
    context = RuntimeContext(
        run=run,
        session=Session(
            session_id="session-deepagents",
            tenant_id="tenant-a",
            user_id="developer",
            agent_name="score-agent",
            agent_version="1.0.0",
            created_at=NOW,
        ),
        workspace=tmp_path,
        assistant_message_id="assistant-deepagents-message",
        resolved_policy=resolved_policy,
        sandbox_command_executor=(cast(Any, _sandbox_executor) if with_sandbox_executor else None),
    )
    gate = DeepagentsToolGate(
        context=context,
        approvals=approvals,
        events=events,
        profiles=default_policy_profiles() if profiles else None,
        policy=None if profiles else PolicyEngine(list(policy_rules or default_policy_rules())),
        quotas=quotas,
        declared_tools=declared_tools,
    )
    return gate, events, context


def _request(name: str, arguments: Mapping[str, Any], tool_call_id: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": dict(arguments), "id": tool_call_id},
        tool=None,
        state={},
        runtime=cast(Any, None),
    )


async def _invoke(
    gate: DeepagentsToolGate,
    *,
    name: str,
    arguments: Mapping[str, Any] | None = None,
    tool_call_id: str = "call-1",
    handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]] | None = None,
) -> ToolMessage | Command[Any]:
    return await gate.awrap_tool_call(
        _request(name, arguments or {}, tool_call_id),
        handler or _echo,
    )


def _denied(output: ToolMessage | Command[Any]) -> bool:
    return isinstance(output, ToolMessage) and output.status == "error"


def _resolved_policy(policy_id: str, rules: list[PolicyRule]) -> ResolvedPolicy:
    return ResolvedPolicy(
        policy_id=policy_id,
        revision=1,
        content_hash="sha256:" + "0" * 64,
        call_policy=PolicyEngine(rules),
        result_policy=ResultPolicyEngine([]),
    )


def test_the_gate_refuses_to_be_built_without_a_policy_source(tmp_path: Path) -> None:
    """Neither engine nor registry: nothing could authorize a call.

    This is the shape of the defect that made every DeepAgents Run fail on its
    first tool call, so it is pinned at the constructor.
    """

    context = RuntimeContext(
        run=Run(
            run_id="run-deepagents",
            session_id="session-deepagents",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="deepagents-gate",
            created_at=NOW,
            updated_at=NOW,
        ),
        session=Session(
            session_id="session-deepagents",
            tenant_id="tenant-a",
            user_id="developer",
            agent_name="score-agent",
            agent_version="1.0.0",
            created_at=NOW,
        ),
        workspace=tmp_path,
    )
    with pytest.raises(ValueError, match="exactly one policy engine or profile registry"):
        DeepagentsToolGate(
            context=context,
            approvals=cast(Any, None),
            events=cast(Any, None),
        )


def test_the_gate_refuses_two_policy_sources(tmp_path: Path) -> None:
    """Two sources would make the authorizing engine depend on resolution order."""

    with pytest.raises(ValueError, match="exactly one policy engine or profile registry"):
        DeepagentsToolGate(
            context=cast(Any, object()),
            approvals=cast(Any, None),
            events=cast(Any, None),
            policy=PolicyEngine(default_policy_rules()),
            profiles=default_policy_profiles(),
        )


@pytest.mark.asyncio
async def test_the_registry_resolves_the_platform_default_when_the_run_declares_none(
    tmp_path: Path,
) -> None:
    gate, _, _ = await _arrange(tmp_path)

    assert vars(gate)["_policy_id"] == "local-standard"
    assert not _denied(await _invoke(gate, name="read_file", arguments={"file_path": "a.txt"}))


@pytest.mark.asyncio
async def test_the_runs_own_policy_wins_over_the_registry(tmp_path: Path) -> None:
    """A published snapshot's policy is authoritative, not the deployment default."""

    gate, events, _ = await _arrange(
        tmp_path,
        resolved_policy=_resolved_policy(
            "tenant-evidence-only",
            [PolicyRule(name="read", tool="Read", decision=PolicyDecision.ALLOW)],
        ),
    )

    # The registry would allow a workspace write; the Run's own policy does not.
    assert _denied(await _invoke(gate, name="write_file", arguments={"file_path": "out.txt"}))
    emitted = await events.list_after("tenant-a", "run-deepagents", 0)
    assert emitted[0].payload["policy_profile"] == "tenant-evidence-only"


@pytest.mark.asyncio
async def test_a_filesystem_tool_is_translated_before_any_rule_runs(tmp_path: Path) -> None:
    gate, events, _ = await _arrange(tmp_path)

    await _invoke(gate, name="write_file", arguments={"file_path": "out.txt"})

    request = (await events.list_after("tenant-a", "run-deepagents", 0))[0]
    assert request.payload["name"] == "Write"
    assert request.payload["runtime_tool_name"] == "write_file"


@pytest.mark.asyncio
async def test_a_write_outside_the_workspace_is_denied_before_policy_runs(
    tmp_path: Path,
) -> None:
    """Containment is not a policy question: an escaping path is never reviewable."""

    gate, events, _ = await _arrange(tmp_path)

    output = await _invoke(gate, name="write_file", arguments={"file_path": "../escape.txt"})

    assert _denied(output)
    assert isinstance(output, ToolMessage)
    assert "must stay within the run workspace" in str(output.content)
    emitted = await events.list_after("tenant-a", "run-deepagents", 0)
    # Only the request and the result: no `tool.allowed` was ever written.
    assert [event.type for event in emitted] == ["tool.request", "tool.result"]
    assert emitted[-1].payload["error"]["code"] == "policy_denied"


@pytest.mark.asyncio
async def test_an_allowed_call_reaches_the_handler_and_is_recorded(tmp_path: Path) -> None:
    gate, events, _ = await _arrange(tmp_path)
    seen: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage | Command[Any]:
        seen.append(str(request.tool_call.get("name") or ""))
        return await _echo(request)

    output = await _invoke(
        gate, name="read_file", arguments={"file_path": "a.txt"}, handler=handler
    )

    assert not _denied(output)
    assert seen == ["read_file"]
    assert [event.type for event in await events.list_after("tenant-a", "run-deepagents", 0)] == [
        "tool.request",
        "tool.allowed",
    ]


@pytest.mark.asyncio
async def test_a_declared_mcp_tool_survives_implicit_deny(tmp_path: Path) -> None:
    """The published tool directory is what makes an MCP tool reachable."""

    gate, _, _ = await _arrange(tmp_path, declared_tools=frozenset({_DECLARED_MCP_TOOL}))

    assert not _denied(await _invoke(gate, name=_DECLARED_MCP_TOOL, arguments={"query": "x"}))


@pytest.mark.asyncio
async def test_an_undeclared_mcp_tool_stays_denied(tmp_path: Path) -> None:
    gate, _, _ = await _arrange(tmp_path)

    assert _denied(await _invoke(gate, name=_DECLARED_MCP_TOOL, arguments={"query": "x"}))


@pytest.mark.asyncio
async def test_a_bundle_operator_needs_both_declaration_and_a_sandbox(tmp_path: Path) -> None:
    """An in-process Bundle operator would run in the Worker, so it is refused."""

    declared = frozenset({_BUNDLE_OPERATOR})
    sandboxed, _, _ = await _arrange(tmp_path, declared_tools=declared, with_sandbox_executor=True)
    worker_local, _, _ = await _arrange(tmp_path, declared_tools=declared)

    assert not _denied(await _invoke(sandboxed, name=_BUNDLE_OPERATOR, arguments={}))
    assert _denied(await _invoke(worker_local, name=_BUNDLE_OPERATOR, arguments={}))


@pytest.mark.asyncio
async def test_a_declared_bundle_operator_cannot_override_explicit_deny(tmp_path: Path) -> None:
    gate, events, _ = await _arrange(
        tmp_path,
        profiles=False,
        policy_rules=[
            PolicyRule(name="blocked-operator", tool=_BUNDLE_OPERATOR, decision=PolicyDecision.DENY)
        ],
        declared_tools=frozenset({_BUNDLE_OPERATOR}),
        with_sandbox_executor=True,
    )
    called = False

    async def handler(request: ToolCallRequest) -> ToolMessage | Command[Any]:
        nonlocal called
        called = True
        return await _echo(request)

    output = await _invoke(gate, name=_BUNDLE_OPERATOR, handler=handler)

    assert _denied(output)
    assert not called
    emitted = await events.list_after("tenant-a", "run-deepagents", 0)
    assert [event.type for event in emitted] == ["tool.request", "tool.result"]
    assert "blocked-operator" in emitted[-1].payload["error"]["message"]


@pytest.mark.asyncio
async def test_a_spent_mcp_quota_denies_before_the_tool_runs(tmp_path: Path) -> None:
    quotas = QuotaService(InMemoryQuotaRepository())
    await quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner-a",
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            limits={QuotaResource.MCP_REQUESTS: 1},
        ),
    )
    gate, events, _ = await _arrange(
        tmp_path,
        declared_tools=frozenset({_DECLARED_MCP_TOOL}),
        quotas=quotas,
    )

    first = await _invoke(gate, name=_DECLARED_MCP_TOOL, tool_call_id="quota-1")
    second = await _invoke(gate, name=_DECLARED_MCP_TOOL, tool_call_id="quota-2")

    assert not _denied(first)
    assert _denied(second)
    emitted = await events.list_after("tenant-a", "run-deepagents", 0)
    assert emitted[-1].payload["error"]["message"] == (
        f"quota exceeded for {QuotaResource.MCP_REQUESTS.value}"
    )


@pytest.mark.asyncio
async def test_the_request_event_is_redacted_for_the_durable_stream(tmp_path: Path) -> None:
    """A secret in tool arguments must not reach the Run event log."""

    gate, events, _ = await _arrange(tmp_path, declared_tools=frozenset({_DECLARED_MCP_TOOL}))

    await _invoke(
        gate,
        name=_DECLARED_MCP_TOOL,
        arguments={"api_key": "sk-live-0123456789abcdef"},
    )

    request = (await events.list_after("tenant-a", "run-deepagents", 0))[0]
    assert "sk-live-0123456789abcdef" not in str(request.payload)
