"""Durable conversational replay and native DeepAgents compaction.

The raw Run event log remains the audit source. The replay checkpoint carries
visible user/assistant messages and the runtime's replacement summary, never
hidden reasoning or raw tool arguments/results. Tools remain available during
the Run and evicted native history is offloaded through the sandbox backend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import Any, cast

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.config import get_stream_writer

from harness.core.manifest import ContextSpec
from harness.runtime.audit_redaction import redact_text

HISTORY_EVENT = "context.history.checkpoint"
_COMPACTION_SCOPE: ContextVar[dict[str, bool] | None] = ContextVar("compaction_scope", default=None)


class CheckedSummarizationMiddleware(SummarizationMiddleware):
    @property
    def name(self) -> str:
        # Replace the built-in slot, rather than running two summarizers.
        return "SummarizationMiddleware"

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        # A shared per-call scope crosses the native gather() summary task safely.
        # The native values update comes only AFTER the answer; report progress
        # as soon as the handler receives the actual rewritten request instead.
        scope = {"started": False}
        token = _COMPACTION_SCOPE.set(scope)

        async def invoke_with_compacted_request(modified_request: Any) -> Any:
            if scope["started"]:
                get_stream_writer()({"harness_context_compaction": "completed"})
                scope["started"] = False
            return await handler(modified_request)

        try:
            return await super().awrap_model_call(request, invoke_with_compacted_request)
        finally:
            _COMPACTION_SCOPE.reset(token)

    async def _acreate_summary(self, messages_to_summarize: Any) -> str:
        # Stream before the real summary call; never infer this phase from token estimates.
        scope = _COMPACTION_SCOPE.get()
        if scope is not None:
            scope["started"] = True
        get_stream_writer()({"harness_context_compaction": "started"})
        summary = await super()._acreate_summary(messages_to_summarize)
        if not summary.strip():
            raise ValueError("context compaction returned an empty summary")
        return summary


def summarization_middleware(model: Any, backend: Any, config: ContextSpec) -> Any:
    defaults = compute_summarization_defaults(model)
    return CheckedSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=(
            ("tokens", config.auto_compact_token_limit)
            if config.auto_compact_token_limit is not None
            else defaults["trigger"]
        ),
        keep=("messages", config.keep_recent_messages),
        # Do not silently drop older evidence from the summarizer's input.
        trim_tokens_to_summarize=None,
        truncate_args_settings=defaults["truncate_args_settings"],
    )


def checkpoint_messages(state: Mapping[str, Any]) -> list[dict[str, str]]:
    messages: Sequence[BaseMessage] = state.get("messages", [])
    event = state.get("_summarization_event")
    if isinstance(event, dict):
        event = cast(dict[str, Any], event)
        cutoff = event.get("cutoff_index")
        summary = event.get("summary_message")
        if (
            isinstance(cutoff, int)
            and 0 <= cutoff <= len(messages)
            and isinstance(summary, HumanMessage)
        ):
            messages = [summary, *messages[cutoff:]]
        else:
            raise ValueError("invalid DeepAgents compaction checkpoint")
    result: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, (HumanMessage, AIMessage)):
            continue
        if isinstance(message, AIMessage) and message.tool_calls:
            continue
        content = message.content
        if isinstance(content, list):
            # Persist visible text only, not provider reasoning/media blocks.
            content = "\n".join(
                block if isinstance(block, str) else str(block.get("text", ""))
                for block in content
                if isinstance(block, str)
                or block.get("type") == "text"
            )
        if content:
            result.append({
                "role": "user" if isinstance(message, HumanMessage) else "assistant",
                "content": redact_text(content, limit=len(content) + 1),
            })
    return result
