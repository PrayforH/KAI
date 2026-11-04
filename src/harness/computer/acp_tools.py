"""ACP is a computer capability, independent of any Bot business workflow."""

import json
from typing import Any, Protocol, cast

from harness.computer.acp_bridge import arguments as acp_arguments
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay
from harness.sandbox.base import SandboxCommandResult, SandboxHandle


class AcpSource(Protocol):
    async def acp_action(
        self, handle: SandboxHandle, payload: dict[str, Any],
    ) -> SandboxCommandResult: ...


def computer_acp_overlay(sandbox: AcpSource, handle: SandboxHandle) -> PlatformToolOverlay:
    async def invoke(arguments: dict[str, Any]) -> dict[str, Any]:
        payload = acp_arguments(arguments)
        try:
            result = await sandbox.acp_action(handle, payload)
            raw: object = json.loads(result.stdout)
            if not isinstance(raw, dict):
                raise ValueError("ACP acknowledgement is malformed")
            value = cast(dict[str, Any], raw)
            if type(value.get("ok")) is not bool:
                raise ValueError("ACP acknowledgement is malformed")
        except (RuntimeError, ValueError):
            return {"isError": True, "content": [{"type": "text",
                "text": "ACP 调用未完成；请检查连接、登录与授权目录，不要假报已执行"}]}
        # ACP session handles are transport bookkeeping, not conversation
        # content. Keep them out of the model's answer surface.
        public = {k: v for k, v in value.items() if k != "session_id"}
        return {"isError": result.exit_code != 0 or not value["ok"],
                "content": [{"type": "text", "text": json.dumps(public, ensure_ascii=False)}]}

    return PlatformToolOverlay(tools=(PlatformTool(
        name="computer_acp",
        description=("通过本地电脑已安装的 ACP 调用 Claude Code 或 Codex，复用机主现有登录。"
                     "当前仅只读调研：Claude plan / Codex read-only；权限请求拒绝。"
                     "cwd 须为已授权项目目录，可省略使用默认项目；返回真实执行结果。"),
        schema={"type": "object", "additionalProperties": False,
                "properties": {
                    "agent": {"type": "string", "enum": ["claude", "codex"]},
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 6000},
                    "cwd": {"type": "string", "description": "本地电脑已授权项目的绝对路径"},
                    "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 180},
                }, "required": ["agent", "prompt"]},
        handler=invoke,
    ),), instructions=(
        "电脑提供 computer_acp，可用用户在这台电脑上现有的 Claude Code/Codex 登录进行只读调研。"
        "承接编码任务时可调用该能力；普通 shell 的独立任务账号登录与它不同。"
        "不复制登录凭据，不改权限模式；调用失败如实说明，不把自己的回答当成外部 Agent 的结果。"
    ))
