from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from harness.agent_package import pack_agent_package
from harness.promotion import (
    PromotionEntry,
    PromotionError,
    PromotionPlan,
    ReleasePromotionClient,
)
from harness.release import ReleaseManifest, create_release_manifest


def release(tmp_path: Path) -> tuple[Path, ReleaseManifest]:
    root = tmp_path / "release"
    agents = root / "agents"
    agents.mkdir(parents=True)
    archive, _report = pack_agent_package(
        "agents/echo-agent/agent.yaml", output_directory=agents
    )
    sboms = root / "sbom"
    sboms.mkdir()
    for component in ("api", "web", "sandbox"):
        (sboms / f"{component}.json").write_text(
            json.dumps({"spdxVersion": "SPDX-2.3"}), encoding="utf-8"
        )
    (root / "RELEASE_NOTES.md").write_text(
        "## [0.1.0]\n\n### Added\n\n- Test release.\n", encoding="utf-8"
    )
    manifest = create_release_manifest(
        artifact_root=root,
        platform_version="0.1.0",
        release_notes_path=root / "RELEASE_NOTES.md",
        source_commit="a" * 40,
        bundle_paths=(archive,),
        image_references={
            "api": f"registry/api@sha256:{'a' * 64}",
            "web": f"registry/web@sha256:{'b' * 64}",
            "sandbox": f"registry/sandbox@sha256:{'c' * 64}",
        },
        sbom_paths={
            component: sboms / f"{component}.json"
            for component in ("api", "web", "sandbox")
        },
    )
    return root, manifest


def test_promote_checks_hashes_gates_and_pins_signed_image(tmp_path: Path) -> None:
    root, manifest = release(tmp_path)
    requests: list[httpx.Request] = []
    promotion_keys: list[str] = []
    canary_healthy = "snapshot-old"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal canary_healthy
        requests.append(request)
        path = request.url.path
        if path == "/v1/agents/bundles":
            bundle = manifest.agent_bundles[0]
            return httpx.Response(
                201,
                json={
                    "name": bundle.name,
                    "version": bundle.version,
                    "manifest_hash": bundle.manifest_hash,
                    "package_hash": bundle.package_hash,
                },
            )
        if path.endswith("/quality-gate") or "/evaluation-gates/" in path:
            return httpx.Response(200, json={"passed": True})
        if path.endswith("/deployment-snapshots"):
            bundle = manifest.agent_bundles[0]
            return httpx.Response(
                200,
                json=[
                    {
                        "snapshotId": "snapshot-test",
                        "agentVersion": bundle.version,
                        "packageHash": bundle.package_hash,
                        "imageDigest": f"sha256:{'c' * 64}",
                        "config": {"RELEASE_ID": manifest.release_id},
                    }
                ],
            )
        if path.endswith("/environments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "test",
                        "revision": 8,
                        "healthySnapshotId": "snapshot-test",
                    },
                    {
                        "name": "canary",
                        "revision": 4,
                        "healthySnapshotId": canary_healthy,
                    }
                ],
            )
        if path == "/v1/studio/deployments/promote":
            body = cast(dict[str, object], json.loads(request.content))
            assert body["imageDigest"] == f"sha256:{'c' * 64}"
            assert body["expectedEnvironmentRevision"] == 4
            promotion_keys.append(cast(str, body["idempotencyKey"]))
            return httpx.Response(
                202,
                json={
                    "deployment": {
                        "deploymentId": "deployment-new",
                        "previousSnapshotId": "snapshot-old",
                        "targetSnapshotId": "snapshot-new",
                    }
                },
            )
        if path == "/v1/studio/deployments/deployment-new":
            canary_healthy = "snapshot-new"
            return httpx.Response(
                200,
                json={
                    "deployment": {
                        "deploymentId": "deployment-new",
                        "status": "succeeded",
                        "previousSnapshotId": "snapshot-old",
                        "targetSnapshotId": "snapshot-new",
                    },
                    "target": {"imageDigest": f"sha256:{'c' * 64}"},
                },
            )
        raise AssertionError(path)

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://harness.test"
    )
    client = ReleasePromotionClient(
        base_url="https://harness.test",
        service_token="x" * 32,
        tenant_id="tenant-a",
        user_id="release-bot",
        client=http,
        poll_seconds=0,
    )

    plan = client.promote(
        artifact_root=root,
        manifest=manifest,
        operation_id="run-100-attempt-1",
        environment="canary",
        execution_profile="isolated-default",
        canary_percent=10,
    )
    retry_plan = client.promote(
        artifact_root=root,
        manifest=manifest,
        operation_id="run-100-attempt-2",
        environment="canary",
        execution_profile="isolated-default",
        canary_percent=10,
    )

    assert plan.release_id == manifest.release_id
    assert plan.operation_id == "run-100-attempt-1"
    assert retry_plan.operation_id == "run-100-attempt-2"
    assert plan.entries[0].previous_snapshot_id == "snapshot-old"
    assert len(promotion_keys) == 2
    assert promotion_keys[0] != promotion_keys[1]
    assert all(len(key) < 256 for key in promotion_keys)
    assert all(request.headers["x-harness-service-token"] == "x" * 32 for request in requests)


def test_promotion_stops_before_environment_when_eval_gate_fails(tmp_path: Path) -> None:
    root, manifest = release(tmp_path)
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/agents/bundles":
            bundle = manifest.agent_bundles[0]
            return httpx.Response(
                201,
                json={
                    "name": bundle.name,
                    "version": bundle.version,
                    "manifest_hash": bundle.manifest_hash,
                    "package_hash": bundle.package_hash,
                },
            )
        return httpx.Response(200, json={"passed": False})

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://harness.test"
    )
    client = ReleasePromotionClient(
        base_url="https://harness.test",
        service_token="x" * 32,
        tenant_id="tenant-a",
        user_id="release-bot",
        client=http,
        poll_seconds=0,
    )

    with pytest.raises(PromotionError, match="Eval gate blocked"):
        client.promote(
            artifact_root=root,
            manifest=manifest,
            operation_id="run-101-attempt-1",
            environment="test",
            execution_profile="isolated-default",
            canary_percent=10,
        )

    assert not any(path.endswith("/environments") for path in paths)


def test_partial_promotion_rolls_back_before_returning_failure(tmp_path: Path) -> None:
    root, manifest = release(tmp_path)
    checkpoints: list[PromotionPlan] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        bundle = manifest.agent_bundles[0]
        if path == "/v1/agents/bundles":
            return httpx.Response(
                201,
                json={
                    "name": bundle.name,
                    "version": bundle.version,
                    "manifest_hash": bundle.manifest_hash,
                    "package_hash": bundle.package_hash,
                },
            )
        if path.endswith("/quality-gate") or "/evaluation-gates/" in path:
            return httpx.Response(200, json={"passed": True})
        if path.endswith("/environments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "test",
                        "revision": 1,
                        "healthySnapshotId": "snapshot-old",
                    }
                ],
            )
        if path == "/v1/studio/deployments/promote":
            return httpx.Response(
                202,
                json={
                    "deployment": {
                        "deploymentId": "deployment-new",
                        "previousSnapshotId": "snapshot-old",
                        "targetSnapshotId": "snapshot-new",
                    }
                },
            )
        if path == "/v1/studio/deployments/deployment-new":
            return httpx.Response(
                200,
                json={
                    "deployment": {"status": "succeeded"},
                    "target": {"imageDigest": f"sha256:{'c' * 64}"},
                },
            )
        raise AssertionError(path)

    class TrackingClient(ReleasePromotionClient):
        rolled_back = False

        def rollback(self, plan: PromotionPlan) -> PromotionPlan:
            self.rolled_back = True
            return plan

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://harness.test"
    )
    client = TrackingClient(
        base_url="https://harness.test",
        service_token="x" * 32,
        tenant_id="tenant-a",
        user_id="release-bot",
        client=http,
        poll_seconds=0,
    )

    with pytest.raises(PromotionError, match="healthy environment snapshot"):
        client.promote(
            artifact_root=root,
            manifest=manifest,
            operation_id="run-102-attempt-1",
            environment="test",
            execution_profile="isolated-default",
            canary_percent=100,
            checkpoint=checkpoints.append,
        )

    assert client.rolled_back is True
    assert len(checkpoints) == 1
    assert checkpoints[0].operation_id == "run-102-attempt-1"
    assert len(checkpoints[0].entries) == 1


def test_repeated_rollback_keeps_the_original_recovery_target(tmp_path: Path) -> None:
    _root, manifest = release(tmp_path)
    rollback_keys: list[str] = []
    rollback_targets: list[str] = []
    healthy_snapshot = "snapshot-failed"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal healthy_snapshot
        path = request.url.path
        if path.endswith("/environments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "canary",
                        "revision": 9,
                        "healthySnapshotId": healthy_snapshot,
                    }
                ],
            )
        if path.endswith("/canary/rollback"):
            body = cast(dict[str, object], json.loads(request.content))
            rollback_keys.append(cast(str, body["idempotencyKey"]))
            rollback_targets.append(cast(str, body["snapshotId"]))
            healthy_snapshot = "snapshot-old"
            return httpx.Response(
                202,
                json={"deployment": {"deploymentId": "rollback-stable"}},
            )
        if path == "/v1/studio/deployments/rollback-stable":
            return httpx.Response(
                200,
                json={
                    "deployment": {
                        "status": "succeeded",
                        "targetSnapshotId": "snapshot-old",
                    }
                },
            )
        raise AssertionError(path)

    http = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://harness.test"
    )
    client = ReleasePromotionClient(
        base_url="https://harness.test",
        service_token="x" * 32,
        tenant_id="tenant-a",
        user_id="release-bot",
        client=http,
        poll_seconds=0,
    )
    plan = PromotionPlan(
        releaseId=manifest.release_id,
        operationId="run-103-attempt-1",
        environment="canary",
        entries=(
            PromotionEntry(
                agentName=manifest.agent_bundles[0].name,
                environment="canary",
                previousSnapshotId="snapshot-old",
                targetSnapshotId="snapshot-failed",
                deploymentId="promotion-failed",
            ),
        ),
    )

    first = client.rollback(plan)
    second = client.rollback(first)

    assert first == plan
    assert second == plan
    assert rollback_targets == ["snapshot-old", "snapshot-old"]
    assert rollback_keys[0] == rollback_keys[1]


def test_rollback_rejects_a_stale_healthy_environment_pointer(tmp_path: Path) -> None:
    _root, manifest = release(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/environments"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "canary",
                        "revision": 9,
                        "healthySnapshotId": "snapshot-failed",
                    }
                ],
            )
        if path.endswith("/canary/rollback"):
            return httpx.Response(
                202,
                json={"deployment": {"deploymentId": "rollback-stale"}},
            )
        if path == "/v1/studio/deployments/rollback-stale":
            return httpx.Response(
                200,
                json={
                    "deployment": {
                        "status": "succeeded",
                        "targetSnapshotId": "snapshot-old",
                    }
                },
            )
        raise AssertionError(path)

    client = ReleasePromotionClient(
        base_url="https://harness.test",
        service_token="x" * 32,
        tenant_id="tenant-a",
        user_id="release-bot",
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://harness.test"
        ),
        poll_seconds=0,
    )
    plan = PromotionPlan(
        releaseId=manifest.release_id,
        operationId="run-104-attempt-1",
        environment="canary",
        entries=(
            PromotionEntry(
                agentName=manifest.agent_bundles[0].name,
                environment="canary",
                previousSnapshotId="snapshot-old",
                targetSnapshotId="snapshot-failed",
                deploymentId="promotion-failed",
            ),
        ),
    )

    with pytest.raises(PromotionError, match="without restoring"):
        client.rollback(plan)
