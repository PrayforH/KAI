"""Verify that every shipped surface and the release ref use one SemVer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.versioning import audit_platform_version


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected")
    parser.add_argument("--require-released", action="store_true")
    args = parser.parse_args()
    audit = audit_platform_version(
        Path.cwd(), expected=args.expected, require_released=args.require_released
    )
    print(
        json.dumps(
            audit.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
