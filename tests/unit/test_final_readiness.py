import re
from pathlib import Path

from harness.readiness import audit_repository


def test_repository_has_complete_goal_and_release_evidence() -> None:
    audit = audit_repository(Path.cwd())

    assert re.fullmatch(r"\d+\.\d+\.\d+", audit.platform_version)
    assert audit.goal_reports == tuple(f"G{index:02d}" for index in range(20))
    assert audit.external_actions_pinned >= 10
    assert audit.custom_role_decision == "fixed-versioned-roles"


def _newest_migration_revision() -> str:
    """The newest revision on disk, so this test cannot go stale.

    A literal pin here has to be edited by hand every time a migration lands — it read
    0033 while the repository was at 0035, and the gate then failed for a reason that
    said nothing about the code.
    """

    revisions = sorted(
        match.group(1)
        for path in (Path.cwd() / "migrations" / "versions").glob("*.py")
        if (match := re.search(r'^revision(?:: str)? = "([^"]+)"', path.read_text(), re.MULTILINE))
    )
    assert revisions, "no migrations found"
    return revisions[-1]


def test_readiness_reports_the_newest_migration_as_head() -> None:
    audit = audit_repository(Path.cwd())

    assert audit.migration_head == _newest_migration_revision()
