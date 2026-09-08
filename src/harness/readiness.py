"""Static repository audit for the G00-G19 production-readiness evidence set."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import Field

from harness.release import ReleaseModel
from harness.versioning import audit_platform_version


class ReadinessAudit(ReleaseModel):
    platform_version: str = Field(alias="platformVersion")
    migration_head: str = Field(alias="migrationHead")
    goal_reports: tuple[str, ...] = Field(alias="goalReports")
    workflows: tuple[str, ...]
    external_actions_pinned: int = Field(alias="externalActionsPinned", ge=1)
    custom_role_decision: str = Field(alias="customRoleDecision")


def audit_repository(root: Path) -> ReadinessAudit:
    repository = root.resolve()
    version = audit_platform_version(repository)
    config = Config(str(repository / "alembic.ini"))
    config.set_main_option("script_location", str(repository / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise ValueError(f"expected one migration head, found {heads}")

    report_ids: set[str] = set()
    for path in (repository / "docs/plans").glob("*g??-*.md"):
        match = re.search(r"-g(\d{2})-", path.name)
        if match:
            report_ids.add(f"G{match.group(1)}")
    expected = {f"G{index:02d}" for index in range(20)}
    if report_ids != expected:
        raise ValueError(f"Goal evidence is incomplete: missing {sorted(expected - report_ids)}")

    workflow_names = ("verify.yml", "release.yml", "promote.yml")
    action_count = 0
    for name in workflow_names:
        path = repository / ".github/workflows" / name
        text = path.read_text(encoding="utf-8")
        if not isinstance(yaml.safe_load(text), dict):
            raise ValueError(f"workflow is not a YAML object: {name}")
        for reference in re.findall(
            r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", text, re.MULTILINE
        ):
            if reference.startswith("./"):
                continue
            action_count += 1
            if re.fullmatch(r"[^@]+@[a-f0-9]{40}", reference) is None:
                raise ValueError(f"external Action is not pinned by full SHA: {reference}")

    role_decision = repository / "docs/runbooks/final-production-readiness.md"
    role_text = role_decision.read_text(encoding="utf-8")
    if "User-defined roles are deliberately not part of the current release" not in role_text:
        raise ValueError("custom role extension decision is missing")

    return ReadinessAudit(
        platformVersion=version.platform_version,
        migrationHead=heads[0],
        goalReports=tuple(sorted(report_ids)),
        workflows=workflow_names,
        externalActionsPinned=action_count,
        customRoleDecision="fixed-versioned-roles",
    )
