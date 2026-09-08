"""Create or verify the deterministic release supply-chain manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.release import (
    REQUIRED_IMAGES,
    create_release_manifest,
    load_release_manifest,
    verify_release_manifest,
    write_release_manifest,
)


def _assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or key not in REQUIRED_IMAGES or not item:
            raise ValueError(f"expected component=value for {sorted(REQUIRED_IMAGES)}")
        if key in result:
            raise ValueError(f"duplicate component: {key}")
        result[key] = item
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--artifact-root", type=Path, required=True)
    create.add_argument("--platform-version", required=True)
    create.add_argument("--release-notes", type=Path, required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--image", action="append", default=[])
    create.add_argument("--sbom", action="append", default=[])
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--artifact-root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--expected-commit")
    verify.add_argument("--required-schema-version")
    args = parser.parse_args()

    if args.command == "create":
        images = _assignments(args.image)
        sboms = {
            component: Path(path) for component, path in _assignments(args.sbom).items()
        }
        manifest = create_release_manifest(
            artifact_root=args.artifact_root,
            platform_version=args.platform_version,
            release_notes_path=args.release_notes,
            source_commit=args.source_commit,
            bundle_paths=sorted((args.artifact_root / "agents").glob("*.zip")),
            image_references=images,
            sbom_paths=sboms,
        )
        write_release_manifest(manifest, args.output)
        print(f"READY release-sha256:{manifest.release_id}")
        return

    manifest = load_release_manifest(args.manifest)
    verify_release_manifest(
        manifest,
        artifact_root=args.artifact_root,
        expected_commit=args.expected_commit,
        required_schema_version=args.required_schema_version,
    )
    print(f"VERIFIED release-sha256:{manifest.release_id}")


if __name__ == "__main__":
    main()
