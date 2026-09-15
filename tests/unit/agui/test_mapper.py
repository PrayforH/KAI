import json
from datetime import UTC, datetime

from harness.agui.mapper import map_harness_event
from harness.core.events import RunEvent


def event(event_type: str, payload: dict[str, object] | None = None, sequence: int = 1) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence}",
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        sequence=sequence,
        type=event_type,
        timestamp=datetime.now(UTC),
        payload=payload or {},
    )


def test_maps_run_and_text_lifecycle_to_standard_events() -> None:
    started = map_harness_event(event("run.queued"))
    text = map_harness_event(event("message.delta", {"text": "hello"}, 2))
    finished = map_harness_event(event("run.succeeded", sequence=3))

    assert [item.model_dump(by_alias=True)["type"] for item in started] == [
        "RUN_STARTED",
        "ACTIVITY_SNAPSHOT",
    ]
    assert text[0].model_dump(by_alias=True) == {
        "type": "TEXT_MESSAGE_CONTENT",
        "timestamp": None,
        "rawEvent": None,
        "messageId": "assistant-run-1",
        "delta": "hello",
    }
    assert [item.model_dump(by_alias=True)["type"] for item in finished] == [
        "ACTIVITY_DELTA",
        "RUN_FINISHED",
    ]


def test_can_keep_message_text_and_artifacts_activity_only() -> None:
    text = map_harness_event(
        event("message.delta", {"text": "处理中"}, 2),
        project_response_text=False,
    )
    artifact = map_harness_event(
        event("artifact.ready", {"artifact_id": "artifact-1"}, 3),
        project_artifact=False,
    )

    assert [item.model_dump(by_alias=True)["type"] for item in text] == [
        "ACTIVITY_DELTA"
    ]
    assert [item.model_dump(by_alias=True)["type"] for item in artifact] == [
        "ACTIVITY_DELTA"
    ]


def test_historical_provider_diagnostic_is_sanitized_in_text_replay() -> None:
    projected = map_harness_event(
        event("message.delta", {"text": "API Error: 400 Content Exists Risk"})
    )
    dumped = [item.model_dump(by_alias=True) for item in projected]

    assert dumped[0]["delta"] == (
        "模型服务拒绝了本轮上下文，可能由输入或外部检索内容触发。"
        "请重新运行，或缩小主题与时间范围。"
    )
    assert "Content Exists Risk" not in repr(dumped)


def test_maps_tool_and_domain_events_to_tool_calls() -> None:
    tool = map_harness_event(
        event(
            "tool.request",
            {"tool_call_id": "tool-1", "name": "Read", "arguments": {"path": "a"}},
        )
    )
    approval = map_harness_event(
        event(
            "approval.requested",
            {
                "approval_id": "approval-1",
                "tool_call_id": "tool-1",
                "reason": "Write requires approval",
                "message_id": "assistant-approval-segment",
                "tool_name": "Write",
                "argument_summary": {"file_path": "outputs/report.md"},
                "sandbox_provider": "local",
                "sandbox_isolation": "workspace",
                "policy_rule": "write-review",
                "risk": "medium",
            },
        )
    )
    artifact = map_harness_event(
        event(
            "artifact.ready",
            {
                "artifact_id": "artifact-1",
                "name": "result.txt",
                "media_type": "text/plain",
                "size_bytes": 21,
            },
        )
    )

    assert [item.model_dump(by_alias=True)["type"] for item in tool] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "ACTIVITY_DELTA",
    ]
    assert [item.model_dump(by_alias=True)["type"] for item in approval] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "ACTIVITY_DELTA",
    ]
    assert approval[0].model_dump(by_alias=True)["toolCallName"] == (
        "harness_request_approval"
    )
    assert approval[0].model_dump(by_alias=True)["parentMessageId"] == (
        "assistant-approval-segment"
    )
    assert json.loads(approval[1].model_dump(by_alias=True)["delta"]) == {
        "approval_id": "approval-1",
        "run_id": "run-1",
        "tool_call_id": "tool-1",
        "reason": "Write requires approval",
        "tool_name": "Write",
        "argument_summary": {"file_path": "outputs/report.md"},
        "sandbox_provider": "local",
        "sandbox_isolation": "workspace",
        "policy_rule": "write-review",
        "risk": "medium",
    }
    assert artifact[0].model_dump(by_alias=True)["toolCallName"] == (
        "harness_present_artifact"
    )
    assert json.loads(artifact[1].model_dump(by_alias=True)["delta"]) == {
        "artifact_id": "artifact-1",
        "run_id": "run-1",
        "name": "result.txt",
        "media_type": "text/plain",
        "size_bytes": 21,
    }


def test_keeps_subagent_events_as_versioned_activity() -> None:
    activity = map_harness_event(event("subagent.started", {"name": "researcher"}))

    assert activity[0].model_dump(by_alias=True)["name"] == "harness.subagent.v1"
    assert activity[1].model_dump(by_alias=True)["type"] == "ACTIVITY_DELTA"


def test_maps_approval_decision_to_matching_tool_result() -> None:
    requested = map_harness_event(
        event("approval.requested", {"approval_id": "approval-1"}, sequence=2)
    )
    approved = map_harness_event(
        event("approval.approved", {"approval_id": "approval-1"}, sequence=3)
    )

    assert requested[0].model_dump(by_alias=True)["toolCallId"] == (
        "harness-approval-approval-1"
    )
    assert approved[0].model_dump(by_alias=True)["type"] == "TOOL_CALL_RESULT"
    assert approved[0].model_dump(by_alias=True)["toolCallId"] == (
        "harness-approval-approval-1"
    )
    assert approved[1].model_dump(by_alias=True)["type"] == "ACTIVITY_DELTA"


def test_artifact_projection_completes_its_tool_call() -> None:
    projected = map_harness_event(
        event("artifact.ready", {"artifact_id": "artifact-1", "name": "result.txt"})
    )

    assert [item.model_dump(by_alias=True)["type"] for item in projected] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "ACTIVITY_DELTA",
    ]
    assert projected[-2].model_dump(by_alias=True)["toolCallId"] == (
        "harness-artifact-artifact-1"
    )


def test_runtime_result_sets_immediate_activity_status_and_metrics() -> None:
    projected = map_harness_event(
        event(
            "runtime.result",
            {"is_error": False, "num_turns": 2, "total_cost_usd": 0.012},
        )
    )

    patch = projected[-1].model_dump(by_alias=True)["patch"]
    assert {"op": "replace", "path": "/status", "value": "succeeded"} in patch
    assert {"op": "add", "path": "/metrics/turns", "value": 2} in patch
    assert {"op": "add", "path": "/metrics/cost_usd", "value": 0.012} in patch


def test_run_failure_keeps_safe_code_and_recovery_message() -> None:
    projected = map_harness_event(
        event(
            "run.failed",
            {
                "error_code": "provider_content_rejected",
                "message": "模型服务拒绝了本轮上下文，请重新运行。",
            },
        )
    )

    error = projected[-1].model_dump(by_alias=True)
    assert error["type"] == "RUN_ERROR"
    assert error["code"] == "provider_content_rejected"
    assert error["message"] == "模型服务拒绝了本轮上下文，请重新运行。"


def test_expired_and_cancelled_approvals_close_matching_tool_cards() -> None:
    for decision in ("expired", "cancelled"):
        mapped = map_harness_event(event(f"approval.{decision}", {"approval_id": "approval-1"}))
        result = mapped[0].model_dump(by_alias=True)
        assert result["type"] == "TOOL_CALL_RESULT"
        assert result["toolCallId"] == "harness-approval-approval-1"
        assert json.loads(result["content"]) == {"decision": decision}
