import base64

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from harness.runtime.message_mapper import (
    map_sdk_message,
    provider_error_user_message,
    provider_result_error_code,
)


def test_maps_assistant_text_and_tool_use() -> None:
    message = AssistantMessage(
        content=[
            TextBlock(text="hello"),
            ToolUseBlock(id="tool-1", name="Read", input={"file_path": "a.txt"}),
        ],
        model="claude-sonnet-4-6",
    )

    events = map_sdk_message(message)

    assert [event.type for event in events] == ["message.delta", "tool.request"]
    assert events[0].payload == {"text": "hello"}
    assert events[1].payload == {
        "tool_call_id": "tool-1",
        "name": "Read",
        "arguments": {"file_path": "a.txt"},
    }


def test_sdk_provider_diagnostics_do_not_enter_message_events() -> None:
    diagnostic = (
        "Failed to authenticate. API Error: 403 token quota is not enough "
        "(request id: private-request-id)"
    )
    assistant = AssistantMessage(
        content=[TextBlock(text=diagnostic)],
        model="synthetic",
    )
    stream = StreamEvent(
        uuid="event-provider-error",
        session_id="session-1",
        parent_tool_use_id=None,
        event={
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": diagnostic},
        },
    )

    events = [*map_sdk_message(assistant), *map_sdk_message(stream)]

    assert {event.payload["text"] for event in events} == {
        "模型服务拒绝了本轮请求。请打开运行详情查看状态，并稍后重试。"
    }
    assert "private-request-id" not in repr(events)
    assert "quota" not in repr(events)


def test_content_risk_diagnostic_is_safe_and_actionable() -> None:
    diagnostic = "API Error: 400 Content Exists Risk"
    assistant = AssistantMessage(
        content=[TextBlock(text=diagnostic)],
        model="synthetic",
    )

    events = map_sdk_message(assistant)
    error_code = provider_result_error_code(diagnostic, 400)

    assert error_code == "provider_content_rejected"
    assert events[0].payload["text"] == provider_error_user_message(error_code)
    assert "Content Exists Risk" not in repr(events)


def test_maps_partial_text_and_subagent_lifecycle() -> None:
    partial = StreamEvent(
        uuid="event-1",
        session_id="session-1",
        parent_tool_use_id="agent-tool-1",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}},
    )

    events = map_sdk_message(partial)

    assert [event.type for event in events] == ["subagent.delta"]
    assert events[0].payload == {
        "parent_tool_use_id": "agent-tool-1",
        "text": "x",
    }


def test_child_assistant_text_never_enters_main_message_stream() -> None:
    message = AssistantMessage(
        content=[TextBlock(text="child answer")],
        model="claude-sonnet-4-6",
        parent_tool_use_id="agent-tool-1",
    )

    events = map_sdk_message(message)

    assert [event.type for event in events] == ["subagent.delta"]
    assert events[0].payload == {
        "parent_tool_use_id": "agent-tool-1",
        "text": "child answer",
    }


def test_maps_stream_message_lifecycle() -> None:
    started = StreamEvent(
        uuid="event-start",
        session_id="session-1",
        parent_tool_use_id=None,
        event={"type": "message_start", "message": {}},
    )
    stopped = StreamEvent(
        uuid="event-stop",
        session_id="session-1",
        parent_tool_use_id=None,
        event={"type": "message_stop"},
    )

    assert [event.type for event in map_sdk_message(started)] == ["message.start"]
    assert [event.type for event in map_sdk_message(stopped)] == ["message.completed"]


def test_maps_sdk_task_lifecycle_to_neutral_runtime_events() -> None:
    started = TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id="task-1",
        description="Review architecture",
        uuid="start-1",
        session_id="session-1",
        tool_use_id="tool-1",
        task_type="helper",
    )
    progress = TaskProgressMessage(
        subtype="task_progress",
        data={},
        task_id="task-1",
        description="Review architecture",
        usage={"total_tokens": 12, "tool_uses": 2, "duration_ms": 300},
        uuid="progress-1",
        session_id="session-1",
        tool_use_id="tool-1",
        last_tool_name="Read",
    )
    completed = TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id="task-1",
        status="completed",
        output_file="/private/never-show",
        summary="Found two boundary issues",
        uuid="complete-1",
        session_id="session-1",
        tool_use_id="tool-1",
        usage={"total_tokens": 20, "tool_uses": 3, "duration_ms": 500},
    )
    failed = TaskUpdatedMessage(
        subtype="task_updated",
        data={},
        task_id="task-2",
        patch={"status": "failed", "secret": "never-show"},
        status="failed",
        session_id="session-1",
    )

    events = [
        map_sdk_message(started)[0],
        map_sdk_message(progress)[0],
        map_sdk_message(completed)[0],
        map_sdk_message(failed)[0],
    ]

    assert [event.type for event in events] == [
        "runtime.task.started",
        "runtime.task.updated",
        "runtime.task.completed",
        "runtime.task.failed",
    ]
    assert events[0].payload == {
        "task_id": "task-1",
        "description": "Review architecture",
        "status": "running",
        "parent_tool_use_id": "tool-1",
        "task_type": "helper",
    }
    assert events[1].payload["last_tool_name"] == "Read"
    assert events[2].payload["summary"] == "Found two boundary issues"
    assert "never-show" not in repr(events)


def test_generic_system_message_whitelists_status_metadata() -> None:
    message = SystemMessage(
        subtype="status",
        data={
            "session_id": "session-1",
            "status": "compacting",
            "secret": "never-show",
        },
    )

    events = map_sdk_message(message)

    assert events[0].payload == {
        "subtype": "status",
        "session_id": "session-1",
        "status": "compacting",
    }
    assert "never-show" not in repr(events)


def test_init_message_keeps_safe_tool_and_mcp_connection_metadata() -> None:
    message = SystemMessage(
        subtype="init",
        data={
            "session_id": "session-1",
            "tools": ["Read", "mcp__tavily__tavily_search", {"secret": "drop"}],
            "mcp_servers": [
                {"name": "tavily", "status": "connected", "url": "never-show"},
                {"name": "broken", "status": "failed", "error": "never-show"},
            ],
            "apiKey": "never-show",
        },
    )

    events = map_sdk_message(message)

    assert events[0].payload == {
        "subtype": "init",
        "session_id": "session-1",
        "tools": ["Read", "mcp__tavily__tavily_search"],
        "mcp_servers": [
            {"name": "tavily", "status": "connected"},
            {"name": "broken", "status": "failed"},
        ],
    }
    assert "never-show" not in repr(events)


def test_noisy_or_unknown_system_messages_are_not_persisted() -> None:
    for subtype in ("thinking_tokens", "background_tasks_changed", "future_noise"):
        message = SystemMessage(
            subtype=subtype,
            data={"session_id": "session-1", "detail": "never-show"},
        )

        assert map_sdk_message(message) == []


def test_result_event_contains_cost_but_not_prompt_or_secret() -> None:
    result = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="claude-session",
        total_cost_usd=0.01,
        usage={
            "input_tokens": 11,
            "output_tokens": 7,
            "cache_read_input_tokens": 3,
            "service_tier": "standard",
            "secret": "never-show",
        },
    )

    events = map_sdk_message(result)

    assert events[0].type == "runtime.result"
    assert events[0].payload == {
        "subtype": "success",
        "is_error": False,
        "num_turns": 1,
        "session_id": "claude-session",
        "total_cost_usd": 0.01,
        "stop_reason": None,
        "duration_ms": 10,
        "duration_api_ms": 8,
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "cache_read_input_tokens": 3,
        },
    }
    assert "never-show" not in repr(events)


def test_error_result_normalizes_inconsistent_success_subtype() -> None:
    result = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=0,
        is_error=True,
        num_turns=1,
        session_id="claude-session",
        api_error_status=403,
    )

    event = map_sdk_message(result)[0]

    assert event.payload["subtype"] == "api_error_403"


def test_internal_task_result_metadata_is_redacted() -> None:
    message = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="task-1",
                content=[
                    {
                        "type": "text",
                        "text": (
                            "This tool result is internal metadata. "
                            "agentId: secret output_file: /private/tmp/secret"
                        ),
                    }
                ],
                is_error=False,
            )
        ],
        uuid="user-1",
        parent_tool_use_id=None,
        tool_use_result=None,
    )

    events = map_sdk_message(message)

    assert events[0].payload["content"] == "[Internal tool metadata omitted]"
    assert "agentId" not in repr(events)
    assert "/private/tmp" not in repr(events)


def test_inline_image_tool_result_keeps_no_base64_in_events() -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"pixels" * 32).decode()
    message = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="read-1",
                content=[
                    {"type": "text", "text": "Image returned inline (image/png, 12 bytes)."},
                    {"type": "image", "data": payload, "mimeType": "image/png"},
                ],
                is_error=False,
            )
        ],
        uuid="user-1",
        parent_tool_use_id=None,
        tool_use_result=None,
    )

    events = map_sdk_message(message)

    content = events[0].payload["content"]
    assert content[0]["text"].startswith("Image returned inline")
    placeholder = content[1]
    assert placeholder["type"] == "image"
    assert placeholder["media_type"] == "image/png"
    assert placeholder["base64_chars"] == len(payload)
    assert "omitted" in placeholder
    # Run events are persisted and replayed to browsers: the pixels must not be
    # part of any durable or streamed payload.
    assert payload not in repr(events)


def test_anthropic_style_image_block_is_summarized_too() -> None:
    payload = base64.b64encode(b"fakejpegbytes" * 8).decode()
    message = UserMessage(
        content=[
            ToolResultBlock(
                tool_use_id="read-2",
                content=[
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": payload,
                        },
                    }
                ],
                is_error=False,
            )
        ],
        uuid="user-2",
        parent_tool_use_id=None,
        tool_use_result=None,
    )

    events = map_sdk_message(message)

    placeholder = events[0].payload["content"][0]
    assert placeholder["media_type"] == "image/jpeg"
    assert placeholder["base64_chars"] == len(payload)
    assert payload not in repr(events)


def test_maps_only_provider_thinking_text_not_signatures():
    message = AssistantMessage(
        content=[ThinkingBlock(thinking="核对输入。", signature="private-signature")], model="model"
    )
    events = map_sdk_message(message)
    assert events[0].type == "reasoning.delta"
    assert events[0].payload == {"text": "核对输入。", "block_index": 0}
    assert "private-signature" not in repr(events)
    signature = StreamEvent(
        uuid="sig",
        session_id="s",
        parent_tool_use_id=None,
        event={
            "type": "content_block_delta",
            "delta": {"type": "signature_delta", "signature": "private-signature"},
        },
    )
    assert map_sdk_message(signature) == []
