from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import SystemMessage
from pydantic import ValidationError

from harness.core.manifest import ContextSpec
from harness.runtime.codex_protocol import map_codex_notification
from harness.runtime.message_mapper import map_sdk_message


def test_context_settings_validate_runtime_and_headroom() -> None:
    with pytest.raises(ValidationError, match="below"):
        ContextSpec(autoCompactTokenLimit=4096, contextWindowTokens=4096)
    with pytest.raises(ValueError, match="Claude uses"):
        ContextSpec(autoCompactTokenLimit=4096).validate_runtime("claude-agent-sdk")
    with pytest.raises(ValueError, match="only supported"):
        ContextSpec(autoCompactPercentage=70).validate_runtime("codex-app-server")
    ContextSpec(autoCompactPercentage=70).validate_runtime("claude-agent-sdk")


def test_codex_context_uses_last_request_not_cumulative_billing_tokens() -> None:
    events = map_codex_notification({
        "method": "thread/tokenUsage/updated",
        "params": {"tokenUsage": {
            "total": {"totalTokens": 900_000},
            "last": {"totalTokens": 12_000, "inputTokens": 11_000},
            "modelContextWindow": 100_000,
        }},
    })
    assert events[0].payload["totalTokens"] == 900_000
    assert events[1].payload["total_tokens"] == 12_000
    assert events[1].payload["percentage"] == 12


def test_native_compaction_events_are_content_free() -> None:
    codex = map_codex_notification({
        "method": "item/completed", "params": {"item": {
            "type": "contextCompaction", "id": "compact-1", "summary": "private",
        }},
    })
    claude = map_sdk_message(SystemMessage(subtype="compact_boundary", data={
        "compact_metadata": {"trigger": "auto", "pre_tokens": 150000, "summary": "private"},
    }))
    assert codex[0].type == claude[0].type == "context.compacted"
    assert claude[0].payload["before_tokens"] == 150000
    assert "private" not in repr([codex, claude])


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_summary", [False, True])
async def test_real_deepagents_compaction_replaces_history_and_survives_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, empty_summary: bool,
) -> None:
    pytest.importorskip("deepagents")
    from deepagents.backends import FilesystemBackend
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field

    from harness.runtime.deepagents_context import HISTORY_EVENT, summarization_middleware
    from tests.unit.runtime.test_deepagents_runtime import _context, _runtime

    class Recorder(BaseChatModel):
        calls: list[str] = Field(default_factory=list)
        summary_calls: int = 0

        @property
        def _llm_type(self) -> str:
            return "compaction-recorder"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None,
                      **kwargs: Any) -> ChatResult:
            text = "\n".join(str(message.content) for message in messages)
            self.calls.append(text)
            if "Context Extraction Assistant" in text:
                self.summary_calls += 1
                assert "上线日期是十月十五日" in text
                reply = "" if empty_summary else "目标：上线日期是十月十五日。待办：验收。"
            else:
                assert "上线日期是十月十五日" in text
                assert "OLD_PAYLOAD" not in text
                reply = "按十月十五日继续验收。"
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])

        async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None,
                             **kwargs: Any) -> ChatResult:
            if "Context Extraction Assistant" not in "\n".join(str(m.content) for m in messages):
                await asyncio.wait_for(allow_answer.wait(), timeout=2)
            return self._generate(messages, stop)

    import asyncio

    from deepagents.middleware.summarization import SummarizationMiddleware

    # The real graph must deliver the start event while the summarizer is still waiting.
    allow_summary = asyncio.Event()
    allow_answer = asyncio.Event()
    original_summary = SummarizationMiddleware._acreate_summary

    async def gated_summary(self: Any, messages: Any) -> str:
        await asyncio.wait_for(allow_summary.wait(), timeout=2)
        return await original_summary(self, messages)

    monkeypatch.setattr(SummarizationMiddleware, "_acreate_summary", gated_summary)
    model = Recorder()
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    middleware = summarization_middleware(
        model, backend, ContextSpec(autoCompactTokenLimit=1024, keepRecentMessages=2)
    )
    graph = create_agent(model, middleware=[middleware])
    runtime = _runtime(monkeypatch, batches=[])
    monkeypatch.setattr(runtime, "_build_graph", lambda *args, **kwargs: graph)
    history = tuple(
        {"role": role, "content": "上线日期是十月十五日。OLD_PAYLOAD " + "旧讨论 " * 1000}
        for _ in range(4) for role in ("user", "assistant")
    ) + ({"role": "user", "content": "保留最新问题"},
         {"role": "assistant", "content": "保留最新答复"})
    context = _context(tmp_path).model_copy(update={"conversation_history": history})
    if empty_summary:
        emitted = []
        with pytest.raises(ValueError, match="empty summary"):
            async for event in runtime.execute(context):
                emitted.append(event)
                if event.type == "context.compaction.started":
                    allow_summary.set()
        assert [e.type for e in emitted if e.type.startswith("context.compaction")] == [
            "context.compaction.started", "context.compaction.failed",
        ]
        assert not any(e.type in {HISTORY_EVENT, "context.compacted"} for e in emitted)
        return
    events = []
    async for event in runtime.execute(context):
        events.append(event)
        if event.type == "context.compaction.started":
            assert model.summary_calls == 0
            allow_summary.set()
        if event.type == "context.compaction.completed":
            allow_answer.set()
    types = [event.type for event in events]
    assert types.index("context.compaction.started") < types.index("context.compaction.completed")
    assert types.index("context.compaction.completed") < types.index("context.compacted")
    assert types.index("context.compacted") < types.index(HISTORY_EVENT)
    assert model.summary_calls == 1
    assert any(event.type == "context.compacted" for event in events)
    checkpoint = next(event for event in events if event.type == HISTORY_EVENT)
    replay = checkpoint.payload["messages"]
    assert "OLD_PAYLOAD" not in repr(replay)
    assert "上线日期是十月十五日" in repr(replay)
    assert replay[-1]["role"] == "assistant"
    assert any(tmp_path.rglob("*.md")), "evicted history must be offloaded"
    visible = "".join(str(e.payload.get("text", "")) for e in events if e.type == "message.delta")
    assert "目标：" not in visible, "summarizer output must not leak into the answer"

    next_context = context.model_copy(update={"conversation_history": tuple(replay)})
    next_events = [event async for event in runtime.execute(next_context)]
    assert next_events[-1].type == "runtime.result"
    assert model.summary_calls == 1


def test_checkpoint_omits_reasoning_and_orphan_tool_messages() -> None:
    pytest.importorskip("deepagents")
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from harness.runtime.deepagents_context import checkpoint_messages

    result = checkpoint_messages({"messages": [
        HumanMessage(content="remember password=private"),
        AIMessage(content="", tool_calls=[{"id": "t", "name": "read", "args": {}}]),
        ToolMessage(content="private tool content", tool_call_id="t"),
        AIMessage(content=[{"type": "reasoning", "text": "hidden"},
                           {"type": "text", "text": "visible answer"}]),
    ]})
    assert [message["role"] for message in result] == ["user", "assistant"]
    assert "private" not in repr(result)
    assert "hidden" not in repr(result)
    assert result[-1]["content"] == "visible answer"


def test_codex_compaction_start_is_content_free() -> None:
    events = map_codex_notification({
        "method": "item/started", "params": {"item": {
            "type": "contextCompaction", "id": "compact-1", "summary": "private",
        }},
    })
    assert events[0].type == "context.compaction.started"
    assert events[0].payload["item_id"] == "compact-1"
    assert "private" not in repr(events)


@pytest.mark.asyncio
async def test_compaction_timeout_emits_failed_before_run_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harness.runtime.base import RuntimeExecutionTimeoutError
    from tests.unit.runtime.test_deepagents_runtime import _context, _runtime

    class TimeoutGraph:
        async def astream(self, *args: Any, **kwargs: Any) -> Any:
            yield "custom", {"harness_context_compaction": "started"}
            raise TimeoutError("private provider diagnostic")

    runtime = _runtime(monkeypatch, batches=[])
    monkeypatch.setattr(runtime, "_build_graph", lambda *args, **kwargs: TimeoutGraph())
    emitted = []
    with pytest.raises(RuntimeExecutionTimeoutError):
        async for item in runtime.execute(_context(tmp_path)):
            emitted.append(item)
    boundaries = [e for e in emitted if e.type.startswith("context.compaction")]
    assert [e.type for e in boundaries] == [
        "context.compaction.started", "context.compaction.failed",
    ]
    assert boundaries[-1].payload["reason"] == "timeout"
    assert "private" not in repr(boundaries)
