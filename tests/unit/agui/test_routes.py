from datetime import UTC, datetime

from harness.agui.routes import (
    active_response_text,
    final_response_text,
    project_stream_event,
)
from harness.core.events import RunEvent


def _event(event_type: str, sequence: int, **payload: object) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence}",
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        sequence=sequence,
        type=event_type,
        timestamp=datetime(2026, 7, 21, tzinfo=UTC),
        payload=payload,
    )


def test_final_response_keeps_all_text_when_no_actions_run() -> None:
    events = [
        _event("message.delta", 1, text="第一段"),
        _event("message.delta", 2, text="第二段"),
        _event("run.succeeded", 3),
    ]

    assert final_response_text(events) == "第一段第二段"


def test_final_response_excludes_progress_commentary_before_last_action() -> None:
    events = [
        _event("message.delta", 1, text="我先检查工作区。"),
        _event("tool.request", 2, tool_name="Bash"),
        _event("tool.allowed", 3, tool_name="Bash"),
        _event("tool.result", 4, tool_name="Bash"),
        _event("message.delta", 5, text="检查完成："),
        _event("message.delta", 6, text="工作区正常。"),
        _event("workspace.archived", 7),
    ]

    assert final_response_text(events) == "检查完成：工作区正常。"


def test_active_response_keeps_progress_in_activity_during_tool_pause() -> None:
    events = [
        _event("message.start", 1, message_id="progress-1"),
        _event(
            "message.delta",
            2,
            message_id="progress-1",
            text="我先检查工作区。",
        ),
        _event("message.completed", 3, message_id="progress-1"),
        _event("tool.request", 4, tool_name="Read"),
        _event("tool.allowed", 5, tool_name="Read"),
    ]

    assert final_response_text(events) == ""
    assert active_response_text(events) == ""


def test_active_response_restores_message_before_any_tool_starts() -> None:
    events = [
        _event("message.start", 1, message_id="answer"),
        _event("message.delta", 2, message_id="answer", text="正在组织答案："),
        _event("message.delta", 3, message_id="answer", text="第一部分。"),
    ]

    assert active_response_text(events) == "正在组织答案：第一部分。"


def test_active_response_replaces_progress_with_new_partial_answer() -> None:
    events = [
        _event("message.delta", 1, message_id="progress", text="正在查询。"),
        _event("tool.request", 2, tool_name="Read"),
        _event("tool.result", 3, tool_name="Read"),
        _event("message.start", 4, message_id="answer"),
        _event("message.delta", 5, message_id="answer", text="查询完成："),
        _event("message.delta", 6, message_id="answer", text="结果正常。"),
    ]

    assert active_response_text(events) == "查询完成：结果正常。"


def test_live_projection_streams_response_then_emits_terminal_artifact() -> None:
    events = [
        _event("run.queued", 1),
        _event("message.start", 2, message_id="progress"),
        _event("message.delta", 3, message_id="progress", text="我先生成文件。"),
        _event("message.completed", 4, message_id="progress"),
        _event(
            "tool.request",
            5,
            tool_call_id="publish-1",
            name="publish_artifact",
            message_id="progress",
        ),
        _event("tool.result", 6, tool_call_id="publish-1"),
        _event("message.start", 7, message_id="final"),
        _event("message.delta", 8, message_id="final", text="文件已经生成。"),
        _event("message.completed", 9, message_id="final"),
        _event(
            "artifact.ready",
            10,
            artifact_id="artifact-1",
            name="报告.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        _event("run.succeeded", 11),
    ]

    queued = [
        item.model_dump(by_alias=True)
        for item in project_stream_event(events[0], events[:1])
    ]
    assert [item["type"] for item in queued] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "ACTIVITY_SNAPSHOT",
    ]
    assert queued[1]["messageId"] == "assistant-run-1"

    progress = [
        item.model_dump(by_alias=True)
        for event in events[1:-1]
        for item in project_stream_event(event, events[: event.sequence])
    ]
    terminal = [
        item.model_dump(by_alias=True)
        for item in project_stream_event(events[-1], events)
    ]

    progress_types = [item["type"] for item in progress]
    assert "TEXT_MESSAGE_CONTENT" in progress_types
    assert [
        item["messageId"]
        for item in progress
        if item["type"] == "TEXT_MESSAGE_START"
    ] == ["progress", "final"]
    assert [
        item["messageId"]
        for item in progress
        if item["type"] == "TEXT_MESSAGE_END"
    ] == ["progress", "final"]
    assert [
        (item["messageId"], item["delta"])
        for item in progress
        if item["type"] == "TEXT_MESSAGE_CONTENT"
    ] == [
        ("progress", "我先生成文件。"),
        ("final", "文件已经生成。"),
    ]
    assert [
        item["parentMessageId"]
        for item in progress
        if item["type"] == "TOOL_CALL_START"
    ] == ["progress"]
    assert "TOOL_CALL_START" not in [
        item.model_dump(by_alias=True)["type"]
        for item in project_stream_event(events[-2], events[:-1])
    ]
    terminal_types = [item["type"] for item in terminal]
    assert terminal_types == [
        "ACTIVITY_DELTA",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "RUN_FINISHED",
    ]
    assert terminal[1]["messageId"] == "assistant-run-1"
    assert terminal[2]["parentMessageId"] == "final"


def test_live_projection_does_not_invent_response_text_before_run_error() -> None:
    queued = _event("run.queued", 1)
    failed = _event("run.failed", 2, message="模型调用失败")

    terminal = [
        item.model_dump(by_alias=True)
        for item in project_stream_event(failed, [queued, failed])
    ]

    assert [item["type"] for item in terminal] == [
        "ACTIVITY_DELTA",
        "TEXT_MESSAGE_END",
        "RUN_ERROR",
    ]
    assert terminal[1]["messageId"] == "assistant-run-1"


def test_live_projection_preserves_completed_response_when_post_processing_fails() -> None:
    events = [
        _event("run.queued", 1),
        _event("tool.request", 2, tool_call_id="write-1", name="Write"),
        _event("tool.result", 3, tool_call_id="write-1"),
        _event("message.start", 4, message_id="provider-final"),
        _event("message.delta", 5, message_id="provider-final", text="图谱已生成。"),
        _event("message.completed", 6, message_id="provider-final"),
        _event(
            "artifact.ready",
            7,
            artifact_id="artifact-graph",
            name="graph.html",
            media_type="text/html",
        ),
        _event("runtime.result", 8, subtype="success", stop_reason="end_turn"),
        _event("run.failed", 9, error_code="workspace_archive_failed"),
    ]

    terminal = [
        item.model_dump(by_alias=True)
        for item in project_stream_event(events[-1], events)
    ]

    assert [item["type"] for item in terminal] == [
        "ACTIVITY_DELTA",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "RUN_ERROR",
    ]
    assert terminal[1]["messageId"] == "assistant-run-1"
    assert terminal[2]["parentMessageId"] == "provider-final"
