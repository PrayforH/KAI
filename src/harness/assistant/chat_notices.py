"""Render durable background receipts as ordinary main-assistant messages.

The inbox remains the source of truth. Reading a receipt does not remove its
message, and updating a run's receipt updates that message instead of appending
another copy. No synthetic user turn, Run, or model call is created.
"""

import re
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


def brief_notice(content: str) -> str:
    """Bound the main-chat projection, retaining blockers from the complete result.

    The source execution and inbox keep the full report. No extra model request or
    fabricated conclusion is used for the chat receipt.
    """
    prose = re.sub(r"```[\s\S]*?(?:```|$)", "", content)
    parts = [re.sub(r"^\s*(?:#{1,6}\s+|[-*]\s+|\d+[.)、]\s*)", "", part).strip()
             for part in re.split(r"(?<=[。！？])\s*|\n+", prose)]
    parts = [part for part in parts if part and not part.startswith("|")]
    if not parts:
        return ""
    critical = [part for part in parts if re.search(
        r"失败|未完成|未验证|未部署|尚未|阻塞|超时|需要你|需要用户|等待你|待确认|待授权|无法|不能",
        part
    )]
    selected = list(dict.fromkeys([parts[0], *(critical[:2] or parts[1:2])]))
    def clip(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
    return "\n\n".join(clip(part, 140) for part in selected[:3])


def notice_text(notice: AssistantInboxItem) -> str:
    # Source identity belongs to the light attribution row, not a body link.
    # A successful Run is not proof that its business objective is complete.
    content = brief_notice(notice.content.strip())
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
