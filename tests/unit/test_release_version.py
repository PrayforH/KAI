import re
from pathlib import Path

import pytest

from harness.versioning import audit_platform_version


def test_every_shipped_surface_and_changelog_use_one_version() -> None:
    audit = audit_platform_version(Path.cwd())

    assert len(set(audit.sources.values())) == 1
    assert audit.platform_version == next(iter(set(audit.sources.values())))
    assert audit.changelog_entry == f"## [{audit.platform_version}]"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", audit.release_state)


def test_requested_release_version_must_match_repository() -> None:
    with pytest.raises(ValueError, match="does not match"):
        audit_platform_version(Path.cwd(), expected="0.0.1")


def test_formal_release_requires_a_dated_changelog(tmp_path: Path) -> None:
    audit = audit_platform_version(Path.cwd())
    changelog = (Path.cwd() / "CHANGELOG.md").read_text(encoding="utf-8")
    dated_heading = f"## [{audit.platform_version}] - {audit.release_state}"
    candidate = changelog.replace(
        dated_heading, f"## [{audit.platform_version}] - Unreleased", 1
    )
    assert candidate != changelog
    repository = tmp_path / "repository"
    repository.mkdir()
    for relative in (
        "pyproject.toml",
        "web/harness-console/package.json",
        "deploy/helm/agent-harness/Chart.yaml",
        "src/harness/__init__.py",
    ):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((Path.cwd() / relative).read_bytes())
    (repository / "CHANGELOG.md").write_text(candidate, encoding="utf-8")

    with pytest.raises(ValueError, match="must have an ISO date"):
        audit_platform_version(
            repository, expected=audit.platform_version, require_released=True
        )
