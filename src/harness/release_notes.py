"""Deterministic release-note extraction from the repository changelog."""

from __future__ import annotations

import re


def extract_release_notes(changelog: str, version: str) -> str:
    """Return exactly one version section, including its heading."""

    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s+-\s+.+)?$", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG does not contain version {version}")
    remaining = changelog[match.end() :]
    next_heading = re.search(
        r"^## \[[^]]+\](?:\s+-\s+.+)?$", remaining, re.MULTILINE
    )
    end = match.end() + next_heading.start() if next_heading is not None else len(changelog)
    notes = changelog[match.start() : end].strip()
    if not notes or not re.search(r"^###\s+", notes, re.MULTILINE):
        raise ValueError(f"CHANGELOG version {version} has no release-note sections")
    return notes + "\n"
