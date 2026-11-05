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
    "你可以操作用户当前执行电脑的可见桌面，computer_* 工具与该电脑的屏幕预览连接同一个桌面会话。"
    "右侧可以独立切换查看其他电脑，查看不等于更换执行电脑。\n"
    "用户要求打开浏览器、访问页面、查看已登录窗口或操作电脑时，使用 computer_* 在这个可见桌面完成，"
    "复用这里的浏览器窗口和登录态；不要另起 headless 浏览器、临时 profile 或第二个虚拟显示来代替。"
    "缺少桌面能力或控制权时如实说明，不把后台请求成功说成桌面已打开。\n"
    "纯命令、文件处理、公开网页检索或明确要求的无界面测试可以在后台完成，这些不会产生可见窗口。\n"
    "截图按像素计费：读文件、看日志、查进程、跑命令一律用 shell，不要截图；"
    "只有必须读取视觉画面时才截图。\n"
    "只看某个区域时传 region（x/y/width/height，单位是桌面像素）；界面偏小可传 scale 0.5–0.8，"
    "两者都能显著降低开销。确需像素定位时可先看全屏，再用 region 复核细节。\n"
    "坐标动作之前要有对应的观测；动作后只在需要确认结果时重新截图。\n"
    "如果用户正在控制电脑，工具会明确提示，请用户在电脑面板点击“交还助理”，等待交还，不要重试输入。"
)

NATIVE_INSTRUCTIONS = (
    "当前电脑已接入 agent-desktop 原生可访问性接口，优先使用 computer_ui，"
    "它操作的仍是真实桌面上已经打开的应用；headless 指无需抢前台焦点的输入方式，"
    "不是另起隐藏浏览器。"
    "不用先截图。先 list_apps/list_windows 定位应用，再 snapshot（默认 interactive+compact；"
    "复杂窗口先 skeleton，再 root 深入）。只使用当前运行返回的完整 @s…:e… ref。"
    "每次界面变化后重新观察；不能凭旧 ref 连续盲点。click/type/select 是严格 headless 语义操作，"
    "不会抢焦点、剪贴板或鼠标。保留并遵守原始 disposition：仅 retry=safe 且 delivery=not_delivered"
    "才可重试；结果不确定先观察，不能重复提交。STALE_REF 重新 snapshot；AMBIGUOUS_TARGET "
    "不要反复重试相同控件。不得为恢复 headless 失败而静默转成坐标输入或 headed。"
    "截图保留给视觉内容与必要核对，不用截图代替可访问性树。"
)

VM_INSTRUCTIONS = (
    "当前执行目标是你的持久 VM 云电脑：后台 shell/文件工具与可见桌面都在这台 VM 上，"
    "后台命令不会自动弹出终端或浏览器窗口。"
    "桌面账号与任务账号有各自的主目录和权限；任务工作区是 /home/kai/workspace，"
    "不是桌面账号的 HOME。工作区产物可通过聊天附件预览或下载；"
    "未经桌面实际核对，不要声称桌面文件应用能直接读取任务目录，也不要绕过账号权限。\n"
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
    *,
    native_desktop: bool = False,
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
            return await sandbox.desktop_action(handle, operation, payload, expected_epoch=epoch)
        except ComputerLeaseLostError as error:
            return _text(f"桌面控制权已变更：{error}")
        except (RuntimeError, ValueError) as error:
            return _text(f"桌面操作未完成：{error}")

    async def screenshot(arguments: dict[str, Any]) -> dict[str, Any]:
        # Forward region/scale untouched: the node validates them, and dropping
        # them here silently turns every cropped view into a full-screen one.
        result = await act("screenshot", dict(arguments))
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

    async def native(arguments: dict[str, Any]) -> dict[str, Any]:
        result = await act("ui", arguments)
        if "content" in result:
            return result
        return _text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))

    native_tools = (
        (
            PlatformTool(
                name="computer_ui",
                description=(
                    "通过 agent-desktop 读取原生控件树、点击控件、填写字段或选择选项；"
                    "优先于截图和坐标动作。结果必须重新观察核对。"
                ),
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "status",
                                "list_apps",
                                "list_windows",
                                "snapshot",
                                "click",
                                "type",
                                "select",
                            ],
                        },
                        "app": {"type": "string", "maxLength": 160},
                        "window_id": {"type": "string", "maxLength": 160},
                        "ref": {
                            "type": "string",
                            "description": "click/type/select：本次运行 snapshot 返回的完整 @s…:e…",
                        },
                        "text": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": "type 的文字，或 select 的选项",
                        },
                        "root": {"type": "string", "description": "snapshot 局部读取的控件 ref"},
                        "surface": {"type": "string", "enum": ["window", "menu", "sheet", "alert"]},
                        "interactive": {"type": "boolean", "default": True},
                        "skeleton": {"type": "boolean", "default": False},
                        "max_depth": {"type": "integer", "minimum": 1, "maximum": 15},
                    },
                    "required": ["action"],
                },
                handler=native,
            ),
        )
        if native_desktop
        else ()
    )

    return PlatformToolOverlay(
        tools=(
            *native_tools,
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
                description='按下组合键，如 ["ctrl", "c"]、["enter"]。',
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
        instructions=(VM_INSTRUCTIONS if handle.provider == "vm-computer" else "")
        + (NATIVE_INSTRUCTIONS + "\n" if native_desktop else "") + INSTRUCTIONS,
    )
