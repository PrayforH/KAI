"""Bot autonomy removes workflow admission, not the tool authorization gate."""

# pyright: reportPrivateUsage=false
from unittest.mock import AsyncMock

import pytest

from harness.policy.models import PolicyDecision, PolicyRule
from harness.runtime.platform_tools import PlatformToolOverlay
from tests.unit.runtime.test_sdk_tool_gate import _arrange, _input


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_sdk_allows_first_action_by_policy_without_classification_or_stop_hook(
    tmp_path, denied
):
    rules = [
        PolicyRule(
            name="shell",
            tool="Bash",
            decision=PolicyDecision.DENY if denied else PolicyDecision.ALLOW,
            priority=100,
        )
    ]
    gate, _, _, events, context = await _arrange(tmp_path, policy_rules=rules)
    context = context.model_copy(
        update={"platform_tools": PlatformToolOverlay(require_public_preface=False)}
    )
    hooks = gate.hooks(context)
    assert "Stop" not in hooks
    result = await hooks["PreToolUse"][0].hooks[0](
        _input("Bash", {"command": "echo first-action"}, "call-1"), "call-1", {"signal": None}
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == ("deny" if denied else "allow")
    assert not any(
        e.type.startswith("assistant.routing")
        for e in await events.list_after("tenant-a", "run-sdk", 0)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_deepagents_keeps_policy_as_the_authority(tmp_path, denied):
    from langchain_core.messages import ToolMessage

    from tests.unit.runtime.test_deepagents_tool_gate import _arrange as arrange_deep
    from tests.unit.runtime.test_deepagents_tool_gate import _invoke

    gate, events, context = await arrange_deep(
        tmp_path,
        profiles=False,
        policy_rules=[
            PolicyRule(
                name="shell",
                tool="Bash",
                decision=PolicyDecision.DENY if denied else PolicyDecision.ALLOW,
                priority=100,
            )
        ],
    )
    gate._context = context.model_copy(
        update={"platform_tools": PlatformToolOverlay(require_public_preface=False)}
    )
    handler = AsyncMock(return_value=ToolMessage(content="done", tool_call_id="call-1"))
    await _invoke(gate, name="execute", arguments={"command": "echo first-action"}, handler=handler)
    assert handler.await_count == (0 if denied else 1)
    assert not any(
        e.type.startswith("assistant.routing")
        for e in await events.list_after("tenant-a", "run-deepagents", 0)
    )
