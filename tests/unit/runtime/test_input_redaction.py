"""A protected path is the evidence, never a list of read commands."""

from __future__ import annotations

import pytest

from harness.runtime.input_redaction import (
    INTERNAL_AGENT_ASSET_MARKER,
    internal_agent_asset_access,
)


def _bash(command: str) -> dict[str, object]:
    return {"name": "Bash", "arguments": {"command": command}}


def _read(path: str) -> dict[str, object]:
    return {"name": "Read", "arguments": {"file_path": path}}


@pytest.mark.parametrize(
    "payload",
    [
        _bash("cat .claude/skills/evidence-reporting/SKILL.md"),
        # The read verb is not what makes it a read: the path is.
        _bash("python3 -c \"print(open('.claude/skills/evidence-reporting/SKILL.md').read())\""),
        _bash("sort /workspace/.harness-runtime/prompts/system.md"),
        _bash("strings workspace/prompts/system.md | head -50"),
        _bash("cat<.claude/skills/x/SKILL.md"),
        _read(".claude/skills/evidence-reporting/SKILL.md"),
        _read("./prompts/system.md"),
        # A tool that is not Read or Bash is decided by the caller's marker alone.
        {"name": "mcp__filesystem__read_text_file", INTERNAL_AGENT_ASSET_MARKER: True},
    ],
)
def test_protected_paths_are_detected_whatever_reads_them(payload: dict[str, object]) -> None:
    assert internal_agent_asset_access(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        _bash("cat outputs/report.md"),
        _bash("python3 -c \"print(open('outputs/report.md').read())\""),
        _read("outputs/report.md"),
        _bash("pwd && ls -la"),
        {"name": "mcp__filesystem__list_directory", INTERNAL_AGENT_ASSET_MARKER: False},
    ],
)
def test_user_visible_work_is_not_redacted(payload: dict[str, object]) -> None:
    assert internal_agent_asset_access(payload) is False
