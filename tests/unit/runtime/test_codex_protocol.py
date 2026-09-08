import pytest

from harness.runtime.codex_protocol import (
    CodexMessageKind,
    CodexNotificationMapper,
    CodexProtocolError,
    classify_codex_message,
    map_codex_notification,
)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ({"id": 1, "result": {}}, CodexMessageKind.RESPONSE),
        ({"id": "1", "error": {"code": -1}}, CodexMessageKind.ERROR),
        ({"method": "turn/started", "params": {}}, CodexMessageKind.NOTIFICATION),
        (
            {"id": 2, "method": "item/fileChange/requestApproval", "params": {}},
            CodexMessageKind.SERVER_REQUEST,
        ),
    ],
)
def test_classifies_app_server_envelopes(
    message: dict[str, object], kind: CodexMessageKind
) -> None:
    assert classify_codex_message(message).kind == kind


@pytest.mark.parametrize(
    "message",
    [
        None,
        [],
        {},
        {"id": True, "result": {}},
        {"result": {}},
        {"id": 1, "result": {}, "error": {}},
    ],
)
def test_rejects_invalid_app_server_envelopes(message: object) -> None:
    with pytest.raises(CodexProtocolError):
        classify_codex_message(message)


def test_maps_thread_and_agent_message_lifecycle() -> None:
    started = map_codex_notification(
        {"method": "thread/started", "params": {"thread": {"id": "thr-1"}}}
    )
    turn = map_codex_notification({"method": "turn/started", "params": {}})
    delta = map_codex_notification(
        {
            "method": "item/agentMessage/delta",
            "params": {"itemId": "item-1", "delta": "hello"},
        }
    )
    completed = map_codex_notification(
        {
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-1", "status": "completed"}},
        }
    )

    events = [*started, *turn, *delta, *completed]

    assert [event.type for event in events] == [
        "runtime.thread.started",
        "message.start",
        "message.delta",
        "message.completed",
        "runtime.turn.completed",
    ]
    assert events[0].payload == {
        "thread_id": "thr-1",
        "runtime": "codex-app-server",
    }
    assert events[2].payload == {"text": "hello"}
    assert events[-1].payload == {"status": "completed"}


def test_maps_command_execution_without_forwarding_unknown_metadata() -> None:
    item = {
        "id": "cmd-1",
        "type": "commandExecution",
        "command": ["pytest", "-q"],
        "cwd": "/workspace",
        "status": "completed",
        "exitCode": 0,
        "aggregatedOutput": "2 passed",
        "privateToken": "never-show",
    }

    started = map_codex_notification({"method": "item/started", "params": {"item": item}})
    completed = map_codex_notification({"method": "item/completed", "params": {"item": item}})

    assert started[0].type == "tool.request"
    assert started[0].payload == {
        "tool_call_id": "cmd-1",
        "name": "Bash",
        "arguments": {"command": ["pytest", "-q"], "cwd": "/workspace"},
    }
    assert completed[0].payload == {
        "tool_call_id": "cmd-1",
        "name": "Bash",
        "status": "completed",
        "exit_code": 0,
        "aggregated_output": "2 passed",
    }
    assert "never-show" not in repr([*started, *completed])


def test_maps_file_changes_as_path_only_tool_metadata() -> None:
    events = map_codex_notification(
        {
            "method": "item/started",
            "params": {
                "item": {
                    "id": "patch-1",
                    "type": "fileChange",
                    "changes": [
                        {"path": "src/app.py", "kind": "update", "diff": "private"},
                        {"path": "tests/test_app.py", "kind": "create"},
                    ],
                }
            },
        }
    )

    assert events[0].payload == {
        "tool_call_id": "patch-1",
        "name": "Edit",
        "arguments": {
            "changes": [
                {"path": "src/app.py", "kind": "update"},
                {"path": "tests/test_app.py", "kind": "create"},
            ]
        },
    }
    assert "private" not in repr(events)


def test_maps_mcp_tool_lifecycle_to_canonical_tool_name() -> None:
    item = {
        "id": "mcp-1",
        "type": "mcpToolCall",
        "server": "sentiment_query_mcp",
        "tool": "search_risk_subjects",
        "arguments": {"keyword": "示例"},
        "status": "completed",
        "result": {"private": "not-forwarded"},
    }

    started = map_codex_notification({"method": "item/started", "params": {"item": item}})
    completed = map_codex_notification({"method": "item/completed", "params": {"item": item}})

    assert started[0].payload == {
        "tool_call_id": "mcp-1",
        "name": "mcp__sentiment_query_mcp__search_risk_subjects",
        "arguments": {"keyword": "示例"},
    }
    assert completed[0].payload == {
        "tool_call_id": "mcp-1",
        "name": "mcp__sentiment_query_mcp__search_risk_subjects",
        "status": "completed",
    }
    assert "not-forwarded" not in repr(completed)


def test_usage_and_errors_are_content_free() -> None:
    usage = map_codex_notification(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "tokenUsage": {
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "reasoningOutputTokens": 3,
                    "private": "never-show",
                }
            },
        }
    )
    error = map_codex_notification(
        {
            "method": "error",
            "params": {
                "error": {
                    "message": "Authorization Bearer private-token",
                    "codexErrorInfo": {"HttpConnectionFailed": {"httpStatusCode": 503}},
                    "additionalDetails": "never-show",
                }
            },
        }
    )

    assert usage[0].payload == {
        "inputTokens": 10,
        "outputTokens": 4,
        "reasoningOutputTokens": 3,
    }
    assert error[0].payload == {
        "code": "HttpConnectionFailed",
        "runtime": "codex-app-server",
        "http_status": 503,
    }
    assert "private-token" not in repr(error)
    assert "never-show" not in repr([*usage, *error])


def test_classifies_other_context_error_without_persisting_remote_message() -> None:
    events = map_codex_notification(
        {
            "method": "error",
            "params": {
                "error": {
                    "message": "context window exceeded with private-token",
                    "codexErrorInfo": {"Other": "too many tokens"},
                }
            },
        }
    )

    assert events[0].payload == {
        "code": "ContextWindowExceeded",
        "runtime": "codex-app-server",
    }
    assert "private-token" not in repr(events)


def test_projects_reasoning_summaries_but_ignores_raw_reasoning() -> None:
    summary = map_codex_notification(
        {
            "method": "item/reasoning/summaryTextDelta",
            "params": {"itemId": "reasoning-1", "delta": "先核对配置，再运行测试。"},
        }
    )

    assert summary[0].type == "reasoning.summary.delta"
    assert summary[0].payload == {
        "text": "先核对配置，再运行测试。",
        "item_id": "reasoning-1",
    }
    for method in ("item/reasoning/textDelta", "future/notification"):
        assert map_codex_notification({"method": method, "params": {"delta": "never-show"}}) == []


def test_native_subagent_events_are_isolated_from_the_lead_message() -> None:
    mapper = CodexNotificationMapper("lead-thread")
    spawn = {
        "id": "collab-1",
        "type": "collabAgentToolCall",
        "tool": "spawnAgent",
        "status": "completed",
        "senderThreadId": "lead-thread",
        "receiverThreadIds": ["child-thread"],
        "prompt": "collect pages 1-10",
        "agentsStates": {"child-thread": {"status": "running"}},
    }

    started = mapper.map(
        {"method": "item/completed", "params": {"threadId": "lead-thread", "item": spawn}}
    )
    child_delta = mapper.map(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "child-thread", "delta": "five rows collected"},
        }
    )
    child_tool = mapper.map(
        {
            "method": "item/started",
            "params": {
                "threadId": "child-thread",
                "item": {
                    "id": "mcp-child",
                    "type": "mcpToolCall",
                    "server": "sentiment",
                    "tool": "search",
                },
            },
        }
    )
    child_completed = mapper.map(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "child-thread",
                "turn": {"status": "completed"},
            },
        }
    )
    lead_completed = mapper.map(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "lead-thread",
                "turn": {"status": "completed"},
            },
        }
    )

    assert [event.type for event in started] == ["subagent.started"]
    assert started[0].payload["task_id"] == "child-thread"
    assert started[0].payload["description"] == "collect pages 1-10"
    assert [event.type for event in child_delta] == ["subagent.delta"]
    assert [event.type for event in child_tool] == ["subagent.updated"]
    assert child_tool[0].payload["last_tool_name"] == "mcp__sentiment__search"
    assert [event.type for event in child_completed] == ["subagent.completed"]
    assert child_completed[0].payload["summary"] == "five rows collected"
    assert [event.type for event in lead_completed] == [
        "message.completed",
        "runtime.turn.completed",
    ]
    assert not any(
        event.type.startswith("message.") or event.type.startswith("runtime.turn")
        for event in (*child_delta, *child_tool, *child_completed)
    )


def test_current_collab_item_name_and_failed_state_are_supported() -> None:
    mapper = CodexNotificationMapper("lead-thread")
    events = mapper.map(
        {
            "method": "item/completed",
            "params": {
                "threadId": "lead-thread",
                "item": {
                    "id": "collab-2",
                    "type": "collabToolCall",
                    "tool": "spawnAgent",
                    "status": "failed",
                    "senderThreadId": "lead-thread",
                    "receiverThreadId": "child-failed",
                    "agentsStates": {
                        "child-failed": {"status": "errored", "message": "upstream failed"}
                    },
                },
            },
        }
    )

    assert [event.type for event in events] == ["subagent.started", "subagent.failed"]
    assert events[-1].payload["task_id"] == "child-failed"
    assert events[-1].payload["summary"] == "upstream failed"
