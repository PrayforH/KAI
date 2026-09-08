"""Extract deterministic release notes for one platform version."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.release_notes import extract_release_notes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    notes = extract_release_notes(args.changelog.read_text(encoding="utf-8"), args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(notes, encoding="utf-8")
    print(f"READY release-notes:{args.version}")


if __name__ == "__main__":
    main()
