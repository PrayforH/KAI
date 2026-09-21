"""All non-run verification for the review fixes, in one container and one process.

The slow part of the earlier scripts was ``list_source_documents``: WeKnora pages a
whole base (up to 50 pages x 100 rows) per call, twice, before anything interesting
happened. The document ids below were read once from those bases and are stable, so the
ownership check needs three engine calls instead of a full listing. Every step prints
its own wall time so the cost of a verification stays visible.
"""

import asyncio
import json
import time

from harness.composition import build_production_container
from harness.config import Settings
from harness.core.errors import NotFoundError
from harness.runtime.input_redaction import internal_agent_asset_access
from harness.runtime.tool_allow_overrides import apply_allow_overrides
from harness.studio.worker_skill_creator import (
    EVALUATION_PATH,
    SKILL_ARCHIVE_SUFFIX,
    _creator_system_prompt,
)

TENANT = "local"
USER = "user_1c16a8994ff548c298c356fd8385eb76"
SOURCE_A = "aipolicy"
SOURCE_B = "overseas"
OWN_DOCUMENT = "c39e269b-ae05-4b08-80ce-93544029f6e3"
FOREIGN_DOCUMENT = "004c366f-bef7-4144-b5de-22cd7094891c"

TIMINGS: dict[str, float] = {}


class Step:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> None:
        self.started = time.monotonic()

    def __exit__(self, *_: object) -> None:
        TIMINGS[self.label] = round(time.monotonic() - self.started, 2)


def bash(command: str) -> bool:
    return internal_agent_asset_access({"name": "Bash", "arguments": {"command": command}})


async def main() -> int:
    report: dict[str, object] = {}
    with Step("settings"):
        settings = Settings()
        report["config"] = {
            "readyTimeout": settings.opensandbox_ready_timeout_seconds,
            "validateTemplate": settings.cubesandbox_validate_template,
            "localEmbeddedDatabase": settings.local_embedded_database,
        }

    with Step("pure-rules"):
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
        report["redaction"] = redaction
        report["creatorPrompt"] = {
            "headings": sum(
                heading in prompt
                for heading in (
                    "## Mission",
                    "## Operating workflow",
                    "## Evidence and tool use",
                    "## Safety boundaries",
                    "## Output contract",
                )
            ),
            "namesArtifacts": f"demo-skill{SKILL_ARCHIVE_SUFFIX}" in prompt
            and EVALUATION_PATH in prompt,
            "overrideModule": apply_allow_overrides.__module__,
        }

    with Step("container"):
        container = build_production_container(settings, execution_enabled=False)

    knowledge = container.knowledge
    with Step("ownership"):
        own = await knowledge.get_source_document(TENANT, USER, SOURCE_A, OWN_DOCUMENT)
        refused: dict[str, str] = {}
        for label, call in (
            (
                "detail",
                knowledge.get_source_document(TENANT, USER, SOURCE_A, FOREIGN_DOCUMENT),
            ),
            (
                "chunks",
                knowledge.list_source_chunks(TENANT, USER, SOURCE_A, FOREIGN_DOCUMENT),
            ),
        ):
            try:
                await call
                refused[label] = "ALLOWED"
            except NotFoundError:
                refused[label] = "refused"
        via_own_source = await knowledge.get_source_document(
            TENANT, USER, SOURCE_B, FOREIGN_DOCUMENT
        )
        report["ownership"] = {
            "ownReads": own.document_id == OWN_DOCUMENT,
            "foreignDetail": refused["detail"],
            "foreignChunks": refused["chunks"],
            "foreignViaItsOwnSource": via_own_source.document_id == FOREIGN_DOCUMENT,
        }

    report["timingsSeconds"] = TIMINGS
    report["verdict"] = (
        "PASS"
        if report["config"]["readyTimeout"] == 180
        and report["config"]["validateTemplate"] is True
        and redaction["pythonOpen"]
        and redaction["sortPrompt"]
        and not redaction["userOutput"]
        and not redaction["plainWork"]
        and report["creatorPrompt"]["headings"] == 5
        and report["creatorPrompt"]["namesArtifacts"] is True
        and report["ownership"]["ownReads"] is True
        and report["ownership"]["foreignDetail"] == "refused"
        and report["ownership"]["foreignChunks"] == "refused"
        and report["ownership"]["foreignViaItsOwnSource"] is True
        else "FAIL"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


raise SystemExit(asyncio.run(main()))
