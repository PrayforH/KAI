"""Verify the deployed redaction rule and the Creator prompt contract on 174."""

import json

from harness.runtime.input_redaction import internal_agent_asset_access
from harness.studio.worker_skill_creator import (
    EVALUATION_PATH,
    SKILL_ARCHIVE_SUFFIX,
    _creator_system_prompt,
)


def bash(command: str) -> bool:
    return internal_agent_asset_access({"name": "Bash", "arguments": {"command": command}})


redaction = {
    "catSkill": bash("cat .claude/skills/x/SKILL.md"),
    "pythonOpen": bash(
        "python3 -c \"print(open('.claude/skills/x/SKILL.md').read())\""
    ),
    "sortPrompt": bash("sort workspace/prompts/system.md"),
    "userOutput": bash("python3 -c \"print(open('outputs/report.md').read())\""),
    "plainWork": bash("pwd && ls -la"),
}

prompt = _creator_system_prompt("demo-skill")
headings = [
    heading
    for heading in (
        "## Mission",
        "## Operating workflow",
        "## Evidence and tool use",
        "## Safety boundaries",
        "## Output contract",
    )
    if heading in prompt
]

report = {
    "redaction": redaction,
    "creatorPromptHeadings": len(headings),
    "creatorPromptNamesArtifacts": f"demo-skill{SKILL_ARCHIVE_SUFFIX}" in prompt
    and EVALUATION_PATH in prompt,
    "creatorPromptNamesAuthoredRoot": "authored/demo-skill" in prompt,
    "verdict": "PASS"
    if redaction["pythonOpen"]
    and redaction["sortPrompt"]
    and redaction["catSkill"]
    and not redaction["userOutput"]
    and not redaction["plainWork"]
    and len(headings) == 5
    and f"demo-skill{SKILL_ARCHIVE_SUFFIX}" in prompt
    else "FAIL",
}
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report["verdict"] == "PASS" else 1)
