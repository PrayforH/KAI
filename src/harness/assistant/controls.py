"""Explicit private-channel controls, separate from model prompts and attachments."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ControlAction = Literal["status", "stop", "steer", "help"]
CONTROL_ALIASES: dict[str, ControlAction] = {
    "/status": "status",
    "进度": "status",
    "查看进度": "status",
    "现在在做什么": "status",
    "正在做什么": "status",
    "/stop": "stop",
    "停止": "stop",
    "停止当前任务": "stop",
    "停止当前执行": "stop",
    "/help": "help",
    "助理帮助": "help",
    "帮助": "help",
}
STEER_PREFIXES = ("/steer ", "/补充 ")
CONTROL_HELP = (
    "直接发文字、图片、文件或语音，与网页继续同一段对话。\n"
    "「进度」查看当前执行；「停止当前任务」请求停止；"
    "「/补充 新要求」补充正在执行的任务。"
)


class ControlIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: ControlAction
    target_run_id: str | None = None
    text: str = Field(default="", max_length=30000)


def parse_control(content: str) -> ControlIntent | None:
    value = content.strip()
    if action := CONTROL_ALIASES.get(value):
        return ControlIntent(action=action)
    for prefix in STEER_PREFIXES:
        if value.startswith(prefix) and (body := value[len(prefix) :].strip()):
            return ControlIntent(action="steer", text=body)
    return None


RUN_LABELS = {
    "queued": "排队中",
    "provisioning": "准备执行环境",
    "running": "正在执行",
    "waiting_approval": "等待你审批，请到网页助理确认",
    "cancelling": "正在停止",
    "cancelled": "已停止",
    "succeeded": "已完成",
    "failed": "执行失败",
    "timed_out": "执行超时",
    "rejected": "执行被拒绝",
}
