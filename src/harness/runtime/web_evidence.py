"""Bounded verification for weather answers on an assistant with public web tools.

A failed forecast in a resumed conversation is history, not a failed request in
the current run. Ask for one actual attempt before accepting that same fallback.
The normal tool policy still decides whether the attempt may execute.
"""

import re
from dataclasses import dataclass

from harness.runtime.web_tools import WEB_TOOL_NAMES

_WEATHER = re.compile(r"天气|气温|预报|\bweather\b|\bforecast\b", re.IGNORECASE)
_TEXT_ONLY = re.compile(r"翻译|改写|润色|解释.{0,8}(词|句)|translate|rephrase", re.IGNORECASE)
_UNAVAILABLE = re.compile(
    r"未获取到|无法.{0,20}(获取|查询|联网|预报)|"
    r"(没有|不能|不具备).{0,16}(联网|检索|实时|查询)|"
    r"(cannot|unable to|could not).{0,30}(fetch|retrieve|access|weather)",
    re.IGNORECASE,
)

WEB_VERIFICATION_REASON = (
    "本轮平台联网工具可用，但尚未发起天气查询。历史中的失败不是本轮查询结果。"
    "请先通过可用的 WebSearch/WebFetch 实际核实当前预报；首个公开来源失败时换一个来源。"
    "不得编造天气或绕过权限；若真实请求被拒或失败，如实说明即可。"
)


@dataclass
class WebEvidence:
    enabled: bool
    attempted: bool = False
    nudged: bool = False

    @classmethod
    def for_request(cls, *, assistant_scope: bool, prompt: str, tools: set[str]) -> "WebEvidence":
        return cls(enabled=bool(
            assistant_scope and tools.intersection(WEB_TOOL_NAMES.values())
            and _WEATHER.search(prompt) and not _TEXT_ONLY.search(prompt)
        ))

    def note_request(self, name: str) -> None:
        if name in WEB_TOOL_NAMES or name in WEB_TOOL_NAMES.values():
            self.attempted = True

    def needs_verification(self, message: str, *, stop_hook_active: bool = False) -> bool:
        if (
            not self.enabled or self.attempted or self.nudged or stop_hook_active
            or not _UNAVAILABLE.search(message)
        ):
            return False
        self.nudged = True
        return True
