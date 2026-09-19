"""Exercise actual DeepAgents graphs; only model responses and MCP discovery are faked."""

# Graph integration tests intentionally inspect middleware and fixture internals.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("deepagents", reason="requires the DeepAgents extra")

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402

from harness.policy.profiles import read_only_policy_rules  # noqa: E402
from harness.policy.rules import PolicyEngine, default_policy_rules  # noqa: E402
from harness.runtime.deepagents_backend import HarnessSandboxBackend  # noqa: E402
from harness.runtime.deepagents_plan import build_deepagents_plan  # noqa: E402
from harness.runtime.deepagents_runtime import (  # noqa: E402
    DeepagentsRuntime,
    _McpToolsMiddleware,
)
from harness.sandbox.base import SandboxCommandResult  # noqa: E402
from tests.unit.runtime.test_deepagents_runtime import _config  # noqa: E402
from tests.unit.runtime.test_deepagents_tool_gate import _arrange, _resolved_policy  # noqa: E402


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_deny", [False, True])
async def test_discovered_mcp_tool_executes_through_the_policy_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    explicit_deny: bool,
) -> None:
    from harness.policy.models import PolicyDecision, PolicyRule

    name = "mcp__review__lookup"
    called: list[str] = []

    async def lookup(query: str) -> str:
        called.append(query)
        return "found evidence"

    discovered = StructuredTool.from_function(
        coroutine=lookup,
        name=name,
        description="Look up evidence",
    )
    discoveries: list[str] = []

    class Client:
        async def get_tools(self, *, server_name: str) -> list[Any]:
            discoveries.append(server_name)
            return [discovered.model_copy(update={"name": "lookup"})]

    def client_factory(self: Any) -> Client:
        return Client()

    monkeypatch.setattr(_McpToolsMiddleware, "_client", client_factory)
    rules = (
        [PolicyRule(name="blocked-mcp", tool=name, decision=PolicyDecision.DENY)]
        if (explicit_deny)
        else default_policy_rules()
    )
    gate, events, context = await _arrange(
        tmp_path,
        profiles=False,
        policy_rules=rules,
        declared_tools=frozenset({name}),
        with_sandbox_executor=True,
    )
    runtime = DeepagentsRuntime(
        config=replace(
            _config(),
            mcp_servers={"review": {"url": "https://unused.invalid"}},
            declared_tools=frozenset({name}),
        ),
        approvals=gate._approvals,
        events=events,
        policy=PolicyEngine(rules),
    )
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": name, "args": {"query": "hello"}, "id": "call1"}]
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime, "_chat_model", lambda: model)
    plan = build_deepagents_plan(
        builtin_tools=("Read",), permission_policy="default", model="fake", with_mcp=True
    )
    graph = runtime._build_graph(
        context,
        plan=plan,
        backend=HarnessSandboxBackend(
            cast(Any, context.sandbox_command_executor),
            sandbox_id="review",
        ),
    )

    result = await cast(Any, graph).ainvoke({"messages": [{"role": "user", "content": "lookup"}]})

    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(messages) == 1
    assert messages[0].status == ("error" if explicit_deny else "success")
    assert called == ([] if explicit_deny else ["hello"])
    assert "not a valid tool" not in str(messages[0].content)
    assert discoveries == ["review"]
    emitted = await events.list_after("tenant-a", context.run.run_id, 0)
    assert sum(event.type == "tool.request" for event in emitted) == 1
    assert sum(event.type == "tool.allowed" for event in emitted) == (0 if explicit_deny else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_graph_enforces_file_policy_during_real_sandbox_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_only: bool,
) -> None:
    (tmp_path / "evidence.txt").write_text("read-only evidence", encoding="utf-8")
    commands: list[tuple[str, ...]] = []

    async def execute(argv: Any, env: Any, timeout: float) -> SandboxCommandResult:
        commands.append(tuple(argv))
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        return SandboxCommandResult(
            exit_code=process.returncode or 0, stdout=stdout.decode(), stderr=stderr.decode()
        )

    policy_id = "production-read-only" if read_only else "local-standard"
    rules = read_only_policy_rules() if read_only else default_policy_rules()
    gate, events, context = await _arrange(
        tmp_path,
        resolved_policy=_resolved_policy(policy_id, rules),
    )
    context = context.model_copy(
        update={
            "sandbox_command_executor": execute,
            "remote_workspace": str(tmp_path),
        }
    )
    config = _config()
    spec = config.snapshot.manifest.spec
    manifest = config.snapshot.manifest.model_copy(
        update={
            "spec": spec.model_copy(
                update={
                    "permissions": spec.permissions.model_copy(update={"policy": policy_id}),
                }
            )
        }
    )
    runtime = DeepagentsRuntime(
        config=replace(config, snapshot=config.snapshot.model_copy(update={"manifest": manifest})),
        approvals=gate._approvals,
        events=events,
        policy=PolicyEngine(rules),
    )
    model = ScriptedModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "args": {"file_path": "evidence.txt"}, "id": "read1"}
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/out.txt", "content": "review output"},
                        "id": "write1",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(runtime, "_chat_model", lambda: model)
    plan = build_deepagents_plan(
        builtin_tools=("Read", "Write"), permission_policy=policy_id, model="fake"
    )
    graph = runtime._build_graph(
        context,
        plan=plan,
        backend=HarnessSandboxBackend(
            execute,
            sandbox_id="review",
            remote_workspace=str(tmp_path),
        ),
    )

    result = await cast(Any, graph).ainvoke(
        {"messages": [{"role": "user", "content": "read then write"}]}
    )

    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(messages) == 2
    assert messages[0].status == "success"
    assert "read-only evidence" in str(messages[0].content)
    assert commands
    if read_only:
        assert messages[1].status == "error"
        assert "denied by platform policy" in str(messages[1].content)
        assert not (tmp_path / "out.txt").exists()
    else:
        assert messages[1].status == "success"
        assert (tmp_path / "out.txt").read_text() == "review output"
