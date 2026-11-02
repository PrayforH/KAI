"""Server-confirmed control facts, separate from a model's diagnostic prose."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssistantControlReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["work_notifications"] = "work_notifications"
    objective_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    action: Literal["query", "update"]
    summary: str = Field(min_length=1, max_length=300)


def notification_receipt(
    status: dict[str, Any], *, changed: bool,
) -> AssistantControlReceipt | None:
    """Never replace an error, partial result or unknown state with a success card."""
    tasks = status.get("managed")
    if not isinstance(tasks, list) or any(
        not isinstance(task, dict) or task.get("notification_error")
        or not isinstance(task.get("notify_on_progress"), bool) for task in tasks
    ):
        return None
    phase = status.get("status")
    mode = status.get("notification_mode")
    if phase not in {"active", "paused", "completed", "cancelled"} or mode not in {
        "important", "all_results", "none",
    }:
        return None
    if phase == "paused":
        summary = "这项关注已暂停，暂不推送新结果。"
    elif phase in {"completed", "cancelled"}:
        summary = "这项关注已结束，不再推送新结果。"
    elif not tasks:
        summary = "当前没有关联的原任务。"
    elif mode == "none" or not any(task["notify_on_progress"] for task in tasks):
        summary = "结果通知已关闭。"
    elif all(task["notify_on_progress"] for task in tasks):
        summary = "结果通知开关已开启。"
    else:
        enabled = sum(task["notify_on_progress"] for task in tasks)
        summary = f"{enabled}项原任务开启结果通知，{len(tasks) - enabled}项已关闭。"
    if changed:
        summary += "此次只调整通知，没有启动或停止原任务。"
    objective_id, revision = status.get("objective_id"), status.get("revision")
    if (not isinstance(objective_id, str) or not 1 <= len(objective_id) <= 128
            or type(revision) is not int or revision < 1):
        return None
    return AssistantControlReceipt(
        objective_id=objective_id, revision=revision,
        action="update" if changed else "query", summary=summary,
    )
