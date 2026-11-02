from unittest.mock import AsyncMock

import pytest

from harness.agui.activity import activity_projection, build_run_activity
from harness.assistant.receipts import notification_receipt
from harness.core.errors import ConflictError
from tests.unit.agui.test_activity import event
from tests.unit.assistant.test_delegation import FakeGateway, overlay, text_of


def status(**updates):
    return {
        "objective_id": "objective-one", "revision": 2, "status": "active",
        "notification_mode": "important",
        "managed": [{"notify_on_progress": False, "notification_error": None}],
    } | updates


@pytest.mark.parametrize("fields, expected", [
    ({}, "结果通知已关闭。"),
    ({"managed": [{"notify_on_progress": True}]}, "结果通知开关已开启。"),
    ({"status": "paused"}, "这项关注已暂停，暂不推送新结果。"),
    ({"status": "completed"}, "这项关注已结束，不再推送新结果。"),
    ({"managed": []}, "当前没有关联的原任务。"),
    ({"managed": [{"notify_on_progress": True}, {"notify_on_progress": False}]},
     "1项原任务开启结果通知，1项已关闭。"),
])
def test_control_receipt_reports_confirmed_settings_not_model_claims(fields, expected):
    receipt = notification_receipt(status(**fields), changed=False)
    assert receipt is not None and receipt.summary == expected
    changed = notification_receipt(status(**fields), changed=True)
    assert changed is not None and "没有启动或停止原任务" in changed.summary


@pytest.mark.parametrize("fields", [
    {"managed": [{"notify_on_progress": False, "notification_error": "access revoked"}]},
    {"managed": [{"notify_on_progress": "false"}]}, {"managed": None},
    {"status": "unknown"}, {"notification_mode": "unknown"},
    {"objective_id": ""}, {"revision": True},
])
def test_missing_or_failed_confirmation_never_becomes_a_success_receipt(fields):
    assert notification_receipt(status(**fields), changed=False) is None


def test_same_control_fact_is_replayed_in_live_and_history_without_replacing_raw_answer():
    receipt = notification_receipt(status(), changed=False)
    assert receipt is not None
    signal = event("ui.assistant_control", receipt.model_dump(), sequence=3)
    raw = event("message.delta", {"text": "完整诊断 objective-one，revision=2"}, sequence=4)
    history = build_run_activity([event("run.queued"), signal, raw])
    assert history is not None
    live = activity_projection(signal)[0].model_dump(by_alias=True)["patch"][0]["value"]
    assert live == history["items"][1]
    assert history["items"][2]["summary"] == raw.payload["text"]
    invalid = event("ui.assistant_control", receipt.model_dump() | {"extra": 1})
    assert not activity_projection(invalid)


@pytest.mark.asyncio
async def test_receipt_follows_confirmation_and_ui_transport_failure_keeps_original_result():
    gateway = FakeGateway()
    gateway.managed_status = AsyncMock(return_value=status())
    emit = AsyncMock()
    tools = {tool.name: tool.handler for tool in overlay(gateway, emit_receipt=emit).tools}
    result = await tools["assistant_work_notifications"]({"objective_id": "objective-one"})
    assert emit.await_count == 1 and emit.call_args.args[0]["summary"] == "结果通知已关闭。"
    assert '"revision": 2' in text_of(result)  # original diagnostics remain available
    emit.side_effect = RuntimeError("projection unavailable")
    result = await tools["assistant_work_notifications"]({"objective_id": "objective-one"})
    assert '"revision": 2' in text_of(result)
    gateway.managed_status.return_value = status(managed=[{"notify_on_progress": False,
                                                         "notification_error": "revoked"}])
    emit.reset_mock()
    await tools["assistant_work_notifications"]({"objective_id": "objective-one"})
    emit.assert_not_called()
    gateway.configure_managed_notifications = AsyncMock(side_effect=ConflictError("stale revision"))
    result = await tools["assistant_work_notifications"]({
        "objective_id": "objective-one", "managed_id": "managed-one",
        "enabled": False, "expected_revision": 2,
    })
    assert "未能确认" in text_of(result)
    emit.assert_not_called()
