from pathlib import Path

import pytest

from harness.release_notes import extract_release_notes


def test_extracts_only_the_requested_changelog_section() -> None:
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    notes = extract_release_notes(changelog, "0.2.0")

    assert notes.startswith("## [0.2.0] - 2026-08-09\n")
    assert "Context-window telemetry" in notes
    assert "## [0.1.0]" not in notes
    assert notes.endswith("\n")


def test_rejects_missing_or_empty_release_notes() -> None:
    with pytest.raises(ValueError, match="does not contain"):
        extract_release_notes("# Changelog\n", "9.9.9")
    with pytest.raises(ValueError, match="no release-note sections"):
        extract_release_notes("## [1.0.0] - Unreleased\n", "1.0.0")
