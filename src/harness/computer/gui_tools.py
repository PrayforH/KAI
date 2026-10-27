"""Model-facing desktop tools for a personal Computer run.

Ownership follows the desktop lease, not the model's optimism: the first tool
call acquires agent input ownership (only from observation), every action
carries the epoch it acquired, and a user takeover fences stale callers with
a clear message instead of a silent failure.
"""

import json
from typing import Any

from harness.computer.desktop import DesktopControlService, DesktopOccupiedError
from harness.computer.models import ComputerLeaseLostError
from harness.computer.sandbox import ComputerSandboxProvider
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay
from harness.sandbox.base import SandboxHandle

INSTRUCTIONS = (
    "你可以操作用户电脑的可见桌面（与网页右侧显示的是同一台电脑）。\n"
    "先 computer_screenshot 观察当前画面；需要输入时用坐标动作，动作后重新截图确认。\n"
    "如果用户正在控制电脑，工具会明确提示，此时等待用户交还，不要重试输入。"
)


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


Handler = Any


def computer_gui_overlay(
    desktop: DesktopControlService,
    sandbox: ComputerSandboxProvider,
    handle: SandboxHandle,
) -> PlatformToolOverlay:
    tenant_id, user_id, owner = (
        sandbox.session.tenant_id,
        sandbox.session.user_id,
        handle.sandbox_id,
    )

    async def acquire() -> tuple[int | None, str | None]:
        try:
            state = await desktop.acquire_agent(tenant_id, user_id, owner=owner)
        except DesktopOccupiedError as error:
            return None, str(error)
        return state.epoch, None

    async def act(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        epoch, refusal = await acquire()
        if epoch is None:
            return _text(refusal or "桌面控制权暂不可用，请稍后重试。")
        try:
            return await sandbox.desktop_action(
                handle, operation, payload, expected_epoch=epoch
            )
        except ComputerLeaseLostError as error:
            return _text(f"桌面控制权已变更：{error}")
        except (RuntimeError, ValueError) as error:
            return _text(f"桌面操作未完成：{error}")

    async def screenshot(_arguments: dict[str, Any]) -> dict[str, Any]:
        result = await act("screenshot", {})
        content: dict[str, Any] | None = result.get("content")  # type: ignore[assignment]
        if content is not None:
            return result
        png = result.get("png_base64")
        if not isinstance(png, str):
            return _text("截图未返回画面数据。")
        return {
            "content": [
                {
                    "type": "image",
                    "data": png,
                    "mimeType": "image/png",
                },
                {
                    "type": "text",
                    "text": f"桌面画面 {result.get('width')}x{result.get('height')}。",
                },
            ]
        }

    def pointer_tool(operation: str, extra: str) -> PlatformTool:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "横坐标（像素）"},
                "y": {"type": "integer", "description": "纵坐标（像素）"},
            },
            "required": ["x", "y"],
        }
        if extra:
            # The extra JSON is itself a properties object (no required keys).
            schema["properties"].update(json.loads(extra))
        return PlatformTool(
            name=f"computer_{operation}",
            description={
                "move": "移动鼠标指针（不点击）。",
                "click": "在坐标处点击；button 可选 left/middle/right，默认 left。",
                "scroll": "在坐标处滚动；direction 为 up/down，amount 默认 3。",
            }[operation],
            schema=schema,
            handler=lambda arguments, _operation=operation: act(_operation, arguments),
        )

    return PlatformToolOverlay(
        tools=(
            PlatformTool(
                name="computer_screenshot",
                description="获取电脑桌面当前画面截图。观察桌面、确认操作结果时使用。",
                schema={"type": "object", "properties": {}},
                handler=screenshot,
            ),
            pointer_tool("move", ""),
            pointer_tool(
                "click",
                '{"button": {"type": "string", "enum": ["left", "middle", "right"]}}',
            ),
            pointer_tool(
                "scroll",
                '{"direction": {"type": "string", "enum": ["up", "down"]}, '
                '"amount": {"type": "integer", "minimum": 1, "maximum": 10}}',
            ),
            PlatformTool(
                name="computer_type",
                description="向当前焦点窗口输入文字（支持中文）。",
                schema={
                    "type": "object",
                    "properties": {"text": {"type": "string", "maxLength": 2000}},
                    "required": ["text"],
                },
                handler=lambda arguments: act("type", arguments),
            ),
            PlatformTool(
                name="computer_key",
                description="按下组合键，如 [\"ctrl\", \"c\"]、[\"enter\"]。",
                schema={
                    "type": "object",
                    "properties": {
                        "keys": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": 4,
                        }
                    },
                    "required": ["keys"],
                },
                handler=lambda arguments: act("key", arguments),
            ),
        ),
        instructions=INSTRUCTIONS,
    )
