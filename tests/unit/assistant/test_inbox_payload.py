"""Inbox 载荷兼容：旧行没有 metrics/durationSeconds 时仍可读。"""

from harness.assistant.models import AssistantInboxItem


def test_legacy_inbox_payload_without_metering_still_validates() -> None:
    item = AssistantInboxItem.model_validate(
        {
            "itemId": "notice_old",
            "objectiveId": "objective-a",
            "executionId": "execution-old",
            "kind": "succeeded",
            "title": "旧进展",
            "content": "历史载荷",
            "threadId": None,
            "createdAt": "2026-10-01T00:00:00Z",
            "readAt": None,
        }
    )
    assert item.metrics is None
    assert item.duration_seconds is None


def test_terminal_payload_exposes_unknown_usage_without_zero() -> None:
    item = AssistantInboxItem.model_validate(
        {
            "itemId": "notice_uncertain",
            "objectiveId": "objective-a",
            "kind": "succeeded",
            "title": "未计量",
            "content": "本次没有可靠用量",
            "metrics": {"tokens": None, "costUsd": None, "unknownUsage": True},
            "durationSeconds": 12,
            "createdAt": "2026-10-01T00:00:00Z",
        }
    )
    assert item.metrics is not None
    assert item.metrics.tokens is None
    assert item.metrics.unknown_usage is True
    assert item.duration_seconds == 12


def test_skipped_run_is_not_reported_as_unknown_usage() -> None:
    item = AssistantInboxItem.model_validate(
        {
            "itemId": "notice_skipped",
            "objectiveId": "objective-a",
            "kind": "succeeded",
            "title": "巡检职责",
            "content": "本次定时检查未发现变化，未唤起模型。",
            "metrics": {
                "tokens": None,
                "costUsd": None,
                "unknownUsage": False,
                "skippedNoModel": True,
            },
            "durationSeconds": 5,
            "createdAt": "2026-10-01T00:00:00Z",
        }
    )
    assert item.metrics is not None
    assert item.metrics.skipped_no_model is True
    assert item.metrics.unknown_usage is False
    assert item.metrics.tokens is None
