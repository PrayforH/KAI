"""The LangGraph stream must arrive in the platform's own event vocabulary.

The mapper is deliberately structural: it reads chunks and messages by attribute
so it can be tested without LangChain and cannot break when LangChain adds
fields. These tests pin the vocabulary the Worker, the event spine, the quota
ledger and the AG-UI projection all consume.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from harness.runtime.deepagents_events import DeepagentsStreamMapper


def _chunk(content: Any, *, additional_kwargs: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(content=content, additional_kwargs=additional_kwargs or {})


def _ai(tool_calls: list[dict[str, Any]] | None = None, **kwargs: Any) -> Any:
    return SimpleNamespace(
        tool_calls=tool_calls or [],
        usage_metadata=kwargs.get("usage_metadata"),
        content=kwargs.get("content", ""),
        tool_call_id=kwargs.get("tool_call_id"),
        status=kwargs.get("status"),
    )


def _types(events: list[Any]) -> list[str]:
    return [event.type for event in events]


def test_visible_text_opens_a_turn_and_closes_it_once() -> None:
    mapper = DeepagentsStreamMapper(model="m", provider="p")

    assert _types(mapper.messages(_chunk("hello "))) == ["message.start", "message.delta"]
    assert _types(mapper.messages(_chunk("world"))) == ["message.delta"]
    # A model turn that produced text is closed exactly once, when it ends.
    assert _types(mapper.updates({"model": {"messages": [_ai()]}})) == ["message.completed"]
    assert mapper.updates({"model": {"messages": [_ai()]}}) == []


def test_a_turn_without_visible_text_is_not_reported() -> None:
    """A tool-only turn is visible as tool.request, not as an empty message."""

    mapper = DeepagentsStreamMapper()
    mapper.messages(_chunk([{"type": "tool_use", "name": "execute", "input": {}}]))

    assert mapper.updates({"model": {"messages": [_ai()]}}) == []


def test_reasoning_blocks_stream_separately_from_visible_text() -> None:
    mapper = DeepagentsStreamMapper()

    events = mapper.messages(_chunk("answer", additional_kwargs={"reasoning_content": "why"}))

    assert _types(events) == ["reasoning.delta", "message.start", "message.delta"]
    assert events[0].payload == {"text": "why", "block_index": 0}


def test_anthropic_style_thinking_blocks_are_reasoning() -> None:
    mapper = DeepagentsStreamMapper()

    events = mapper.messages(
        _chunk([{"type": "thinking", "thinking": "consider"}, {"type": "text", "text": "ok"}])
    )

    assert _types(events) == ["reasoning.delta", "message.start", "message.delta"]


def test_tool_calls_become_tool_requests_and_close_the_turn() -> None:
    mapper = DeepagentsStreamMapper()
    mapper.messages(_chunk("let me look"))

    events = mapper.updates(
        {
            "model": {
                "messages": [
                    _ai([{"id": "call-1", "name": "execute", "args": {"command": "ls"}}])
                ]
            }
        }
    )

    assert _types(events) == ["tool.request", "message.completed"]
    assert events[0].payload == {
        "tool_call_id": "call-1",
        "name": "execute",
        "arguments": {"command": "ls"},
    }


def test_tool_results_are_reported_from_the_tools_node() -> None:
    mapper = DeepagentsStreamMapper()

    events = mapper.updates(
        {"tools": {"messages": [_ai(tool_call_id="call-1", content="file.txt", status="success")]}}
    )

    assert _types(events) == ["tool.result"]
    assert events[0].payload["tool_call_id"] == "call-1"
    assert events[0].payload["is_error"] is False


def test_failed_tool_results_are_flagged() -> None:
    mapper = DeepagentsStreamMapper()

    events = mapper.updates(
        {"tools": {"messages": [_ai(tool_call_id="call-2", content="boom", status="error")]}}
    )

    assert events[0].payload["is_error"] is True


def test_nested_subgraphs_report_as_subagent_deltas() -> None:
    """A delegated child runs in its own checkpoint namespace."""

    mapper = DeepagentsStreamMapper()

    events = mapper.messages(
        _chunk("child text"),
        {"checkpoint_ns": "parent|researcher:abc"},
    )

    assert _types(events) == ["subagent.delta"]
    assert events[0].payload == {
        "parent_tool_use_id": "parent|researcher:abc",
        "text": "child text",
    }
    # A nested turn never opens or closes the Lead's message.
    assert mapper.updates({"tools": {"messages": []}}) == []


def test_usage_is_accumulated_across_turns() -> None:
    mapper = DeepagentsStreamMapper()
    for usage in ({"input_tokens": 10, "output_tokens": 3}, {"input_tokens": 5}):
        mapper.updates({"model": {"messages": [_ai(usage_metadata=usage)]}})

    result = mapper.result_event(duration_ms=1234)

    assert result.type == "runtime.result"
    assert result.payload["usage"] == {"input_tokens": 15, "output_tokens": 3}
    assert result.payload["duration_ms"] == 1234
    assert result.payload["subtype"] == "success"
    assert result.payload["is_error"] is False
    # At least one turn is reported so a zero-tool Run still looks like a turn.
    assert result.payload["num_turns"] == 1


def test_route_selection_is_announced_once_and_never_binds_a_thread() -> None:
    """Binding a runtime thread would switch off the platform's history replay."""

    mapper = DeepagentsStreamMapper(model="claude-x", provider="anthropic")
    events = mapper.start_events(route_id="route-a")

    assert _types(events) == ["model.route.selected"]
    assert events[0].payload == {
        "route_id": "route-a",
        "provider": "anthropic",
        "model": "claude-x",
        "runtime": "deepagents",
    }
    # The mapper has no way to express a runtime thread at all.
    assert not hasattr(mapper, "thread_event")


def test_non_json_tool_output_is_rendered_for_the_durable_store() -> None:
    mapper = DeepagentsStreamMapper()

    events = mapper.updates(
        {"tools": {"messages": [_ai(tool_call_id="c", content=[{"type": "text", "t": object()}])]}}
    )

    assert isinstance(events[0].payload["content"], list)
    assert isinstance(events[0].payload["content"][0]["t"], str)
