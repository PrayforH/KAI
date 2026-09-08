"""Check GitHub/runner/Harbor prerequisites without reading secret values."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from harness.release_infrastructure import (
    InfrastructureCheck,
    ReleaseInfrastructureAudit,
    audit_release_infrastructure,
    collect_release_infrastructure,
    load_infrastructure_fixture,
)


def _current_repository() -> str:
    process = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0 or not process.stdout.strip():
        raise RuntimeError("cannot determine repository; pass --repo owner/name")
    return process.stdout.strip()


def _error_audit(repository: str, message: str) -> ReleaseInfrastructureAudit:
    return ReleaseInfrastructureAudit(
        repository=repository,
        ready=False,
        checks=(
            InfrastructureCheck(
                checkId="collector.access",
                passed=False,
                detail=message,
            ),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="GitHub owner/name; inferred from the checkout by default")
    parser.add_argument("--fixture", type=Path, help="offline snapshot used by deterministic tests")
    parser.add_argument("--registry", default="harbor.shdata.com:5000")
    parser.add_argument("--namespace", default="agent-studio/amd64")
    args = parser.parse_args()

    repository = args.repo or ("fixture" if args.fixture else _current_repository())
    try:
        snapshot = (
            load_infrastructure_fixture(args.fixture)
            if args.fixture
            else collect_release_infrastructure(repository, registry=args.registry)
        )
        audit = audit_release_infrastructure(
            snapshot,
            expected_registry=args.registry,
            expected_namespace=args.namespace,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        audit = _error_audit(repository, str(exc))

    print(
        json.dumps(
            audit.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if audit.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
