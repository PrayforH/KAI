import pytest

from harness.runtime.codex_protocol import (
    CodexMessageKind,
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
    completed = map_codex_notification(
        {"method": "item/completed", "params": {"item": item}}
    )

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
    }
    assert "private-token" not in repr(error)
    assert "never-show" not in repr([*usage, *error])


def test_unknown_or_reasoning_notifications_are_ignored() -> None:
    for method in (
        "item/reasoning/textDelta",
        "item/reasoning/summaryTextDelta",
        "future/notification",
    ):
        assert map_codex_notification(
            {"method": method, "params": {"delta": "never-show"}}
        ) == []
