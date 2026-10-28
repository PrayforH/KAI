"""Model-facing desktop tools for a personal Computer run.

Ownership follows the desktop lease, not the model's optimism: the first tool
call acquires agent input ownership (only from observation), every action
carries the epoch it acquired, and a user takeover fences stale callers with
a clear message instead of a silent failure.
"""

import json
from typing import Any, Protocol

from harness.computer.desktop import DesktopControlService, DesktopOccupiedError
from harness.computer.models import ComputerLeaseLostError
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay
from harness.sandbox.base import SandboxHandle

INSTRUCTIONS = (
    "你可以操作用户电脑的可见桌面（与网页右侧显示的是同一台电脑）。\n"
    "截图按像素计费：读文件、看日志、查进程、跑命令一律用 shell，不要截图；"
    "只有必须看界面时才截图。\n"
    "只看某个区域时传 region（x/y/width/height，单位是桌面像素）；界面偏小可传 scale 0.5–0.8，"
    "两者都能显著降低开销。先看全屏定位，再用 region 复核细节。\n"
    "坐标动作之前要有对应的观测；动作后只在需要确认结果时重新截图。\n"
    "如果用户正在控制电脑，工具会明确提示，此时等待用户交还，不要重试输入。"
)


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def _tokens(result: dict[str, Any]) -> int:
    """Rough vision cost of this frame; pixels over 750 is the usual estimate."""

    width, height = result.get("width"), result.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        return 0
    return round(width * height / 750)


Handler = Any


class DesktopActionSource(Protocol):
    """What the GUI tools need from the run's sandbox, nothing more."""

    session: Any

    def owns(self, handle: SandboxHandle) -> bool: ...

    async def desktop_action(
        self,
        handle: SandboxHandle,
        operation: str,
        payload: dict[str, Any],
        *,
        expected_epoch: int,
    ) -> dict[str, Any]: ...


def computer_gui_overlay(
    desktop: DesktopControlService,
    sandbox: DesktopActionSource,
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
                    "text": (
                        f"画面 {result.get('width')}x{result.get('height')}"
                        f"（桌面 {result.get('desktop_width')}x{result.get('desktop_height')}"
                        f"，scale {result.get('scale')}，约 {_tokens(result)} tokens）；"
                        "坐标动作一律使用桌面像素，不是这张图的像素。"
                        "只看局部时用 region 或更低的 scale，能显著降低开销。"
                    ),
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
                description=(
                    "获取桌面画面。可只截某个区域（region）或缩小（scale）以降低开销；"
                    "能用 shell 读到的内容不要截图。"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "region": {
                            "type": "object",
                            "description": "可选：只截这块矩形（桌面像素）",
                            "properties": {
                                "x": {"type": "integer", "minimum": 0},
                                "y": {"type": "integer", "minimum": 0},
                                "width": {"type": "integer", "minimum": 1},
                                "height": {"type": "integer", "minimum": 1},
                            },
                            "required": ["x", "y", "width", "height"],
                        },
                        "scale": {
                            "type": "number",
                            "minimum": 0.2,
                            "maximum": 1,
                            "description": "可选：缩放比例，默认 1.0",
                        },
                    },
                },
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
