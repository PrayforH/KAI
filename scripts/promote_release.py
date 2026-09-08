"""Promote or roll back one signed release through the Harness control plane."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from harness.promotion import PromotionPlan, ReleasePromotionClient
from harness.release import load_release_manifest, verify_release_manifest


def _write(plan: PromotionPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            plan.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("promote", "rollback"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--user", default="release-bot")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--environment", choices=("test", "canary", "production"))
    parser.add_argument("--operation-id")
    parser.add_argument("--execution-profile", default="isolated-default")
    parser.add_argument("--canary-percent", type=int)
    args = parser.parse_args()
    token = os.environ.get("HARNESS_API_BEARER_TOKEN", "")
    client = ReleasePromotionClient(
        base_url=args.base_url,
        service_token=token,
        tenant_id=args.tenant,
        user_id=args.user,
    )
    try:
        if args.command == "rollback":
            source = PromotionPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
            _write(client.rollback(source), args.plan)
            return
        if (
            args.artifact_root is None
            or args.manifest is None
            or args.environment is None
            or args.operation_id is None
        ):
            parser.error(
                "promote requires --artifact-root, --manifest, --environment, "
                "and --operation-id"
            )
        manifest = load_release_manifest(args.manifest)
        verify_release_manifest(manifest, artifact_root=args.artifact_root)
        default_percent = 10 if args.environment == "canary" else 100
        percent = (
            args.canary_percent
            if args.canary_percent is not None
            else default_percent
        )
        if not 1 <= percent <= 100:
            parser.error("--canary-percent must be between 1 and 100")
        plan = client.promote(
            artifact_root=args.artifact_root,
            manifest=manifest,
            operation_id=args.operation_id,
            environment=args.environment,
            execution_profile=args.execution_profile,
            canary_percent=percent,
            checkpoint=lambda value: _write(value, args.plan),
        )
        _write(plan, args.plan)
    finally:
        client.close()


if __name__ == "__main__":
    main()
