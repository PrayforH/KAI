"""Routing is enforced before tool side effects, alongside existing policy gates."""

# pyright: reportPrivateUsage=false
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.policy.models import PolicyDecision, PolicyRule
from harness.runtime.platform_tools import PlatformToolOverlay
from tests.unit.runtime.test_sdk_tool_gate import _arrange, _input


def workflow(mode=None):
    state = SimpleNamespace(mode=mode)
    state.before_tool = lambda _name: (
        None if state.mode == "direct" else "先决定路由；派发后不要重复执行"
    )
    state.before_finish = lambda: "先决定路由" if state.mode is None else None
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "dispatch", "clarify"])
async def test_sdk_blocks_execution_before_policy_and_never_marks_it_as_web_attempt(tmp_path, mode):
    gate, _, _, events, context = await _arrange(tmp_path)
    context = context.model_copy(
        update={"platform_tools": PlatformToolOverlay(workflow=workflow(mode))}
    )
    result = await gate.hooks(context)["PreToolUse"][0].hooks[0](
        _input("Bash", {"command": "echo should-not-run"}, "call-1"), "call-1", {"signal": None}
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert [e.type for e in await events.list_after("tenant-a", "run-sdk", 0)] == [
        "assistant.routing.blocked"
    ]


@pytest.mark.asyncio
async def test_direct_still_obeys_original_policy_and_stop_requires_a_decision(tmp_path):
    gate, _, _, _, context = await _arrange(
        tmp_path,
        policy_rules=[
            PolicyRule(
                name="deny-shell",
                tool="Bash",
                decision=PolicyDecision.DENY,
                priority=100,
            )
        ],
    )
    state = workflow()
    context = context.model_copy(update={"platform_tools": PlatformToolOverlay(workflow=state)})
    hooks = gate.hooks(context)
    stop = hooks["Stop"][0].hooks[0]
    assert (await stop({"stop_hook_active": False}, None, {"signal": None}))["decision"] == "block"
    state.mode = "direct"
    assert await stop({"stop_hook_active": False}, None, {"signal": None}) == {}
    result = await hooks["PreToolUse"][0].hooks[0](
        _input("Bash", {"command": "echo forbidden"}, "call-2"), "call-2", {"signal": None}
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_deepagents_blocks_before_the_handler(tmp_path):
    from tests.unit.runtime.test_deepagents_tool_gate import _arrange as arrange_deep
    from tests.unit.runtime.test_deepagents_tool_gate import _invoke

    gate, events, context = await arrange_deep(tmp_path)
    gate._context = context.model_copy(
        update={"platform_tools": PlatformToolOverlay(workflow=workflow("dispatch"))}
    )
    handler = AsyncMock()
    result = await _invoke(
        gate, name="execute", arguments={"command": "echo never"}, handler=handler
    )
    assert "先决定路由" in str(result.content)
    handler.assert_not_awaited()
    assert [e.type for e in await events.list_after("tenant-a", "run-deepagents", 0)] == [
        "assistant.routing.blocked"
    ]
