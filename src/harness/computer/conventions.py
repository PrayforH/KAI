"""Conventions the guest images provide, stated once for the model.

These are contracts of the computer image, not of any single tool: the agent
must not rediscover them (or worse, reinstall them) on every task.
"""

from harness.runtime.platform_tools import PlatformToolOverlay

# Pinned interpreter that carries python-docx, openpyxl and python-pptx. The
# system interpreter is externally managed (PEP 668), so packages go here.
OFFICE_PYTHON = "/home/kai/.venvs/office/bin/python"
WORKSPACE = "/home/kai/workspace"

WORKSPACE_RULES = (
    f"- 工作目录 {WORKSPACE}，产物放这里或它的子目录。\n"
    "- 命令行输出有长度上限，长输出请自行过滤或分页。"
)
OFFICE_RULES = (
    f"- 生成或修改 Word/Excel/PPT 用 {OFFICE_PYTHON}"
    "（已装 python-docx / openpyxl / python-pptx）；"
    f"装其他包用 {OFFICE_PYTHON} -m pip install，不要动系统 Python（被 PEP 668 锁定）。"
)

INSTRUCTIONS = "这台电脑的固定约定（不要每轮重新摸索）：\n" + WORKSPACE_RULES


def computer_conventions_overlay() -> PlatformToolOverlay:
    return PlatformToolOverlay(tools=(), instructions=INSTRUCTIONS)


def office_conventions_overlay() -> PlatformToolOverlay:
    """The office image convention; only providers whose image carries it
    may state it, or the model is promised an interpreter that is not there."""

    return PlatformToolOverlay(tools=(), instructions=INSTRUCTIONS + "\n" + OFFICE_RULES)
