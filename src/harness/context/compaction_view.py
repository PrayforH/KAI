"""Read-only inspection of existing replay checkpoints, not provider requests."""

from typing import Literal, cast

from pydantic import BaseModel

from harness.core.events import RunEvent
from harness.core.ports import EventRepository
from harness.runtime.audit_redaction import redact_text

HISTORY_EVENT = "context.history.checkpoint"
SUMMARY_PREFIX = "You are in the middle of a conversation that has been summarized."


class HistoryMessageView(BaseModel):
    role: str
    content: str
    truncated: bool = False


class HistorySnapshotView(BaseModel):
    source_run_id: str
    message_count: int
    characters: int
    messages: list[HistoryMessageView]
    truncated: bool = False


class CompactionDetail(BaseModel):
    run_id: str
    runtime: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    compaction_count: int
    before: HistorySnapshotView | None = None
    after: HistorySnapshotView | None = None
    summary: HistoryMessageView | None = None


def _messages(event: RunEvent) -> list[dict[str, str]]:
    if event.payload.get("schema_version") != 1:
        return []
    raw = event.payload.get("messages", [])
    if not isinstance(raw, list):
        return []
    messages: list[dict[str, str]] = []
    for value in cast(list[object], raw):
        if not isinstance(value, dict):
            continue
        item = cast(dict[str, object], value)
        role, content = item.get("role"), item.get("content")
        if isinstance(role, str) and role in {"user", "assistant"} and isinstance(content, str):
            messages.append({"role": role, "content": content})
    return messages


def _snapshot(event: RunEvent, messages: list[dict[str, str]]) -> HistorySnapshotView:
    # Bound each lazy detail response; never silently present a clipped view as complete.
    remaining = 240_000
    visible: list[HistoryMessageView] = []
    for item in messages[:200]:
        if remaining <= 0:
            break
        text = redact_text(item["content"], limit=len(item["content"]) + 1)
        length = min(remaining, 120_000)
        visible.append(
            HistoryMessageView(
                role=item["role"],
                content=text[:length],
                truncated=len(text) > length,
            )
        )
        remaining -= len(visible[-1].content)
    return HistorySnapshotView(
        source_run_id=event.run_id,
        message_count=len(messages),
        characters=sum(len(item["content"]) for item in messages),
        messages=visible,
        truncated=len(visible) < len(messages) or any(item.truncated for item in visible),
    )


async def compaction_detail(
    events: EventRepository,
    tenant_id: str,
    session_id: str,
    run_id: str,
) -> CompactionDetail:
    facts = await events.list_after(
        tenant_id,
        run_id,
        0,
        types=("context.compacted", HISTORY_EVENT),
    )
    compactions = [event for event in facts if event.type == "context.compacted"]
    result = CompactionDetail(
        run_id=run_id,
        runtime=str(compactions[-1].payload.get("runtime", "unknown"))
        if compactions
        else "unknown",
        status="unavailable",
        compaction_count=len(compactions),
    )
    if not compactions:
        return result.model_copy(update={"reason": "本轮没有模型上下文压缩记录。"})
    checkpoints = [
        event
        for event in facts
        if event.type == HISTORY_EVENT and event.sequence > compactions[-1].sequence
    ]
    if result.runtime != "deepagents" or not checkpoints:
        return result.model_copy(
            update={"reason": "运行时未提供可展示的压缩后历史检查点；不推测摘要正文。"}
        )
    checkpoint = checkpoints[-1]
    messages = _messages(checkpoint)
    if not messages or not messages[0]["content"].startswith(SUMMARY_PREFIX):
        return result.model_copy(
            update={"reason": "未找到可确认的模型摘要，原始记录仍可在对话中查看。"}
        )
    previous = await events.recent_for_session_types(
        tenant_id,
        session_id,
        (HISTORY_EVENT,),
        limit=1,
        before=compactions[0],
        exclude_run_id=run_id,
    )
    after = _snapshot(checkpoint, messages)
    return result.model_copy(
        update={
            "status": "available",
            "after": after,
            "summary": after.messages[0],
            "before": _snapshot(previous[0], _messages(previous[0])) if previous else None,
        }
    )
