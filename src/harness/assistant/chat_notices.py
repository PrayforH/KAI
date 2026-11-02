"""Render durable background receipts as ordinary main-assistant messages.

The inbox remains the source of truth. Reading a receipt does not remove its
message, and updating a run's receipt updates that message instead of appending
another copy. No synthetic user turn, Run, or model call is created.
"""

from urllib.parse import urlencode

from harness.assistant.models import AssistantInboxItem

NOTICE_MESSAGE_PREFIX = "notice-"
_FAILURES = {"failed", "timed_out", "rejected"}
_STATUS = {
    "succeeded": "已完成", "artifact_ready": "成果已就绪",
    "failed": "未完成", "timed_out": "执行超时", "rejected": "执行被拒绝",
    "cancelled": "已停止", "pending": "已安排", "running": "正在推进",
}


def notice_message_id(notice: AssistantInboxItem) -> str:
    return NOTICE_MESSAGE_PREFIX + notice.item_id


def notice_text(notice: AssistantInboxItem) -> str:
    # Source identity belongs to the light attribution row, not a body link.
    # A successful Run is not proof that its business objective is complete.
    content = notice.content.strip()
    if notice.kind in _FAILURES | {"cancelled", "pending", "running"}:
        return _STATUS[notice.kind] + "。" + ("\n\n" + content if content else "")
    return content or "本次处理已结束。"


def notice_href(notice: AssistantInboxItem) -> str | None:
    if notice.thread_id:
        bot = notice.thread_id.startswith("assistant_specialist_")
        params = {"bot": notice.thread_id.removeprefix("assistant_")} if bot else {
            "thread": notice.thread_id
        }
        if notice.run_id:
            params["run"] = notice.run_id
        if notice.kind in _FAILURES:
            params["trace"] = "1"
            if notice.execution_id:
                params["checkpoint"] = "checkpoint_" + notice.execution_id
        path = "/assistant" if bot else "/"
        return f"{path}?{urlencode(params)}"
    return None
