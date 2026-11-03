"""The assistant speaks before its first tool.

The assistant chat needs one public sentence before the first tool call, otherwise
the user only sees tool silence. The model owns the wording: the gate withholds
the first tool once and asks for a sentence, and the withheld call must never
look like a real step in the public projection.
"""

# pyright: reportPrivateUsage=false

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, UserMessage
from claude_agent_sdk.types import (
    HookMatcher,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from harness.core.manifest import load_manifest
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    ModelCompatibility,
    ModelRoute,
    Run,
    RunStatus,
    Session,
)
from harness.runtime.assistant_preface import PREFACE_NUDGE_REASON, AssistantPreface
from harness.runtime.base import RuntimeContext
from harness.runtime.claude_sdk import ClaudeSdkRuntime
from harness.runtime.platform_tools import PlatformToolOverlay


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="new-api-default",
        provider="new-api",
        base_url="https://new-api.example/v1",
        model="gateway-model",
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use"}),
    )


def _version() -> AgentVersion:
    snapshot = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    return AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="echo-agent",
        version="0.1.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )


class _WithholdingGate:
    """Gate stand-in that withholds the first tool the way the real gate does."""

    def __init__(self) -> None:
        self.withheld: list[str] = []

    def hooks(
        self,
        _context: RuntimeContext,
        *,
        preface: AssistantPreface | None = None,
        **_kwargs: Any,
    ) -> dict[str, list[HookMatcher]]:
        async def hook(hook_input: Any, tool_use_id: str, _hook_context: Any) -> dict[str, Any]:
            if preface is None or not preface.should_nudge():
                return {}
            self.withheld.append(tool_use_id)
            preface.note_nudge(tool_use_id)
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": PREFACE_NUDGE_REASON,
                }
            }

        return {"PreToolUse": [HookMatcher(hooks=[hook])]}


def _context(tmp_path: Path, *, conversation_surface: str) -> RuntimeContext:
    now = datetime.now(UTC)
    return RuntimeContext(
        run=Run(
            run_id="run-preface",
            session_id="session-preface",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="preface",
            created_at=now,
            updated_at=now,
            input={"prompt": "看一下工作区", "conversation_surface": conversation_surface},
        ),
        session=Session(
            session_id="session-preface",
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            created_at=now,
        ),
        workspace=tmp_path,
    )


@pytest.mark.asyncio
async def test_the_withheld_tool_result_never_reaches_the_public_events(
    tmp_path: Path,
) -> None:
    gate = _WithholdingGate()

    async def fake_query(_prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        matcher = options.hooks["PreToolUse"][0]
        await matcher.hooks[0](  # type: ignore[misc]
            {"tool_use_id": "tool-nudged"},
            "tool-nudged",
            {"signal": None},
        )
        yield AssistantMessage(
            content=[
                TextBlock(text="先只读核对工作区。"),
                ToolUseBlock(id="tool-nudged", name="Glob", input={"pattern": "**/*.md"}),
            ],
            model="gateway-model",
        )
        yield UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool-nudged",
                    content=PREFACE_NUDGE_REASON,
                    is_error=True,
                )
            ]
        )
        yield UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool-real",
                    content="2 files",
                    is_error=False,
                )
            ]
        )

    runtime = ClaudeSdkRuntime(
        agent_version=_version(),
        routes=[_route()],
        route_secrets={"new-api-default": "secret"},
        query_factory=fake_query,
        tool_gate=cast(Any, gate),
    )

    events = [
        event
        async for event in runtime.execute(_context(tmp_path, conversation_surface="assistant"))
    ]

    assert gate.withheld == ["tool-nudged"]
    # The public sentence the model produced is kept.
    public_text = "".join(
        str(event.payload.get("text", "")) for event in events if event.type == "message.delta"
    )
    assert "先只读核对工作区。" in public_text
    # The withheld call is not a step; a real tool result still is.
    results = [event for event in events if event.type == "tool.result"]
    assert [str(event.payload.get("tool_call_id", "")) for event in results] == ["tool-real"]
    assert not [event for event in events if event.type == "tool.request"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("surface", "require_preface"), [("task", True), ("assistant", False)])
async def test_tasks_and_specialists_do_not_require_a_preface(
    tmp_path: Path, surface: str, require_preface: bool
) -> None:
    gate = _WithholdingGate()

    async def fake_query(_prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
        if not require_preface:
            assert "Start the assigned work directly" in options.system_prompt
            assert "begin with one short factual progress" not in options.system_prompt
            assert "existing tool approvals remain separate" in options.system_prompt
        # The real gate only nudges when the runtime enabled it.
        matcher = options.hooks["PreToolUse"][0]
        await matcher.hooks[0](  # type: ignore[misc]
            {"tool_use_id": "tool-nudged"},
            "tool-nudged",
            {"signal": None},
        )
        yield UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool-nudged",
                    content="denied",
                    is_error=True,
                )
            ]
        )

    runtime = ClaudeSdkRuntime(
        agent_version=_version(),
        routes=[_route()],
        route_secrets={"new-api-default": "secret"},
        query_factory=fake_query,
        tool_gate=cast(Any, gate),
    )

    context = _context(tmp_path, conversation_surface=surface).model_copy(
        update={"platform_tools": PlatformToolOverlay(require_public_preface=require_preface)}
    )
    events = [event async for event in runtime.execute(context)]

    assert gate.withheld == []
    # Keep the actual tool result: no acknowledgement gate, no hidden step.
    assert [
        str(event.payload.get("tool_call_id", ""))
        for event in events
        if event.type == "tool.result"
    ] == ["tool-nudged"]
