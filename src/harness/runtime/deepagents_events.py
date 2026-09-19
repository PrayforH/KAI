"""Map a LangGraph stream onto the platform's ``RuntimeEvent`` vocabulary.

The Worker only understands the event contract the Claude Agent SDK and Codex
already produce (``message.*``, ``tool.request``, ``tool.result``,
``runtime.result``). This module translates LangGraph's stream into exactly that
vocabulary, so a DeepAgents Run shows up in the console, the event spine, the
quota ledger and the AG-UI projection without any runtime-specific branch.

It deliberately imports nothing from LangChain: chunks are read structurally, so
the mapping is unit-testable and stays stable across LangChain releases that
only add fields.

There is no ``runtime.thread.started`` mapping. Binding a runtime thread would
tell the Worker the session is self-resuming, which switches off the durable
history replay the runtime depends on; DeepAgents v1 has no checkpointer, so it
leaves the session's continuity entirely to the platform.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from harness.runtime.base import RuntimeEvent

# Content-block types that carry visible text / hidden reasoning. Anthropic-style
# models stream both as blocks; OpenAI-compatible gateways use `reasoning_content`.
_TEXT_BLOCK_TYPES = frozenset({"text", "text_delta"})
_REASONING_BLOCK_TYPES = frozenset({"thinking", "thinking_delta", "reasoning", "reasoning_delta"})
_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking")
# A nested subgraph shows up as `parent|child` in the checkpoint namespace.
_SUBGRAPH_SEPARATOR = "|"


def _blocks(content: object) -> list[Mapping[str, Any]]:
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _text_of(content: object) -> str:
    """Visible text of a chunk, ignoring reasoning and tool-call blocks."""

    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in _blocks(content):
        if block.get("type") in _TEXT_BLOCK_TYPES:
            value = block.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


def _reasoning_of(content: object, additional_kwargs: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in _REASONING_KEYS:
        value = additional_kwargs.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    for block in _blocks(content):
        if block.get("type") in _REASONING_BLOCK_TYPES:
            for key in ("thinking", "text", "reasoning"):
                value = block.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
                    break
    return "".join(parts)


def _int_usage(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass
class DeepagentsStreamMapper:
    """Stateful translator for one Run's stream.

    ``messages`` handles token-level chunks; ``updates`` handles completed node
    output, which is where tool calls and tool results are authoritative.
    """

    model: str = ""
    provider: str = ""
    runtime_name: str = "deepagents"
    _turn_text: str = ""
    _turn_open: bool = False
    _input_tokens: int = 0
    _output_tokens: int = 0
    _turns: int = 0
    _tool_calls: int = 0
    _seen_nodes: set[str] = field(default_factory=set)

    # -- token stream --------------------------------------------------------

    def messages(
        self,
        chunk: object,
        metadata: Mapping[str, Any] | None = None,
    ) -> list[RuntimeEvent]:
        meta = metadata or {}
        namespace = meta.get("checkpoint_ns")
        nested = isinstance(namespace, str) and _SUBGRAPH_SEPARATOR in namespace
        parent = str(namespace) if nested else ""
        text = _text_of(getattr(chunk, "content", None))
        reasoning = _reasoning_of(
            getattr(chunk, "content", None),
            getattr(chunk, "additional_kwargs", None) or {},
        )
        events: list[RuntimeEvent] = []
        if reasoning:
            events.append(
                RuntimeEvent(
                    type="subagent.delta" if nested else "reasoning.delta",
                    payload=(
                        {"parent_tool_use_id": parent, "text": reasoning}
                        if nested
                        else {"text": reasoning, "block_index": 0}
                    ),
                )
            )
        if not text:
            return events
        if nested:
            events.append(
                RuntimeEvent(
                    type="subagent.delta",
                    payload={"parent_tool_use_id": parent, "text": text},
                )
            )
            return events
        if not self._turn_open:
            self._turn_open = True
            self._turn_text = ""
            events.append(RuntimeEvent(type="message.start"))
        self._turn_text += text
        events.append(RuntimeEvent(type="message.delta", payload={"text": text}))
        return events

    # -- completed node output ----------------------------------------------

    def updates(self, payload: Mapping[str, Any]) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        for node, update in payload.items():
            if not isinstance(update, dict):
                continue
            self._seen_nodes.add(str(node))
            for message in update.get("messages") or ():
                events.extend(self._message(node=str(node), message=message))
        return events

    def _message(self, *, node: str, message: object) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        # Count each completed model response before branching on tool calls.
        # Middleware may replay messages in its own updates; only the model
        # node reports new model usage, including the final answer's turn.
        if node == "model":
            self._turns += 1
            usage = getattr(message, "usage_metadata", None)
            if isinstance(usage, dict):
                self._input_tokens += _int_usage(usage.get("input_tokens"))
                self._output_tokens += _int_usage(usage.get("output_tokens"))
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                self._tool_calls += 1
                events.append(
                    RuntimeEvent(
                        type="tool.request",
                        payload={
                            "tool_call_id": str(call.get("id") or ""),
                            "name": str(call.get("name") or ""),
                            "arguments": call.get("args") or {},
                        },
                    )
                )
            events.extend(self._close_turn())
            return events
        if node == "tools" or getattr(message, "tool_call_id", None) is not None:
            events.append(
                RuntimeEvent(
                    type="tool.result",
                    payload={
                        "tool_call_id": str(getattr(message, "tool_call_id", "") or ""),
                        "content": _render_content(getattr(message, "content", "")),
                        "is_error": getattr(message, "status", None) == "error",
                    },
                )
            )
            return events
        events.extend(self._close_turn())
        return events

    def _close_turn(self) -> list[RuntimeEvent]:
        """Finish a model turn, but only surface turns that produced visible text."""

        if not self._turn_open:
            return []
        self._turn_open = False
        if not self._turn_text.strip():
            self._turn_text = ""
            return []
        events = [RuntimeEvent(type="message.completed")]
        self._turn_text = ""
        return events

    # -- terminal ------------------------------------------------------------

    def start_events(self, *, route_id: str) -> list[RuntimeEvent]:
        return [
            RuntimeEvent(
                type="model.route.selected",
                payload={
                    "route_id": route_id,
                    "provider": self.provider,
                    "model": self.model,
                    "runtime": self.runtime_name,
                },
            )
        ]

    def result_event(self, *, duration_ms: int, stop_reason: str = "end_turn") -> RuntimeEvent:
        usage = {
            key: value
            for key, value in (
                ("input_tokens", self._input_tokens),
                ("output_tokens", self._output_tokens),
            )
            if value
        }
        return RuntimeEvent(
            type="runtime.result",
            payload={
                "subtype": "success",
                "is_error": False,
                "num_turns": max(1, self._turns),
                "session_id": None,
                "total_cost_usd": None,
                "stop_reason": stop_reason,
                "duration_ms": duration_ms,
                "duration_api_ms": None,
                "usage": usage,
            },
        )


def _render_content(content: object) -> object:
    """Keep tool results bounded and JSON-safe for the durable event store."""

    if isinstance(content, (str, int, float, bool)) or content is None:
        return content
    if isinstance(content, list):
        return [_render_content(item) for item in content]
    if isinstance(content, dict):
        return {str(key): _render_content(value) for key, value in content.items()}
    return str(content)
