"""Release promotion client that preserves immutable bundle and image hashes."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import httpx
from pydantic import Field

from harness.release import ReleaseManifest, ReleaseModel, image_for


class PromotionEntry(ReleaseModel):
    agent_name: str = Field(alias="agentName")
    environment: str
    previous_snapshot_id: str | None = Field(alias="previousSnapshotId")
    target_snapshot_id: str = Field(alias="targetSnapshotId")
    deployment_id: str = Field(alias="deploymentId")


class PromotionPlan(ReleaseModel):
    release_id: str = Field(alias="releaseId")
    operation_id: str = Field(
        alias="operationId",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    environment: str
    entries: tuple[PromotionEntry, ...]


class PromotionError(RuntimeError):
    """A promotion gate or deployment failed without exposing response content."""


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PromotionError(f"{context} returned a non-object response")
    return cast(dict[str, object], value)


def _operation_key(
    kind: str,
    *,
    release_id: str,
    operation_id: str,
    environment: str,
    agent_name: str,
    snapshot_id: str = "",
) -> str:
    material = "\0".join(
        (release_id, operation_id, environment, agent_name, snapshot_id)
    ).encode()
    return f"{kind}:{environment}:{hashlib.sha256(material).hexdigest()}"


class ReleasePromotionClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        tenant_id: str,
        user_id: str,
        client: httpx.Client | None = None,
        poll_seconds: float = 2,
        timeout_seconds: float = 300,
    ) -> None:
        if len(service_token) < 32:
            raise ValueError("a service token of at least 32 characters is required")
        self._client = client or httpx.Client(base_url=base_url, timeout=30)
        self._headers = {
            "X-Harness-Service-Token": service_token,
            "X-Tenant-ID": tenant_id,
            "X-User-ID": user_id,
        }
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds

    def close(self) -> None:
        self._client.close()

    def _json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, object] | None = None,
        content: bytes | None = None,
        media_type: str | None = None,
    ) -> dict[str, object]:
        headers = dict(self._headers)
        if media_type:
            headers["Content-Type"] = media_type
        response = self._client.request(
            method, path, headers=headers, json=body, content=content
        )
        if response.status_code >= 400:
            raise PromotionError(
                f"Harness API rejected {method} {path} with status {response.status_code}"
            )
        try:
            return _object(response.json(), path)
        except ValueError as error:
            raise PromotionError(f"Harness API returned invalid JSON for {path}") from error

    def _publish(self, root: Path, manifest: ReleaseManifest) -> None:
        for bundle in manifest.agent_bundles:
            published = self._json(
                "POST",
                "/v1/agents/bundles",
                content=(root / bundle.path).read_bytes(),
                media_type="application/zip",
            )
            actual = (
                published.get("name"),
                published.get("version"),
                published.get("manifest_hash"),
                published.get("package_hash"),
            )
            expected = (
                bundle.name,
                bundle.version,
                bundle.manifest_hash,
                bundle.package_hash,
            )
            if actual != expected:
                raise PromotionError(
                    f"published Agent hash mismatch: {bundle.name}@{bundle.version}"
                )

    def _gates(self, bundle_name: str, bundle_version: str) -> None:
        eval_gate = self._json(
            "GET",
            f"/v1/studio/evaluation-gates/{bundle_name}/versions/{bundle_version}",
        )
        quality_gate = self._json(
            "GET",
            f"/v1/studio/agents/{bundle_name}/versions/{bundle_version}/quality-gate",
        )
        if eval_gate.get("passed") is not True:
            raise PromotionError(f"Eval gate blocked {bundle_name}@{bundle_version}")
        if quality_gate.get("passed") is not True:
            raise PromotionError(f"quality gate blocked {bundle_name}@{bundle_version}")

    def _environment(self, agent_name: str, environment: str) -> dict[str, object]:
        response = self._client.get(
            f"/v1/studio/agents/{agent_name}/environments", headers=self._headers
        )
        if response.status_code >= 400:
            raise PromotionError(
                f"Harness API rejected environment lookup with status {response.status_code}"
            )
        try:
            values = response.json()
        except ValueError as error:
            raise PromotionError("environment lookup returned invalid JSON") from error
        if not isinstance(values, list):
            raise PromotionError("environment lookup returned a non-list response")
        for value in cast(list[object], values):
            item = _object(value, "environment lookup")
            if item.get("name") == environment:
                return item
        raise PromotionError(f"environment is unavailable: {agent_name}/{environment}")

    def _require_prior_environment(
        self,
        *,
        manifest: ReleaseManifest,
        agent_name: str,
        agent_version: str,
        package_hash: str,
        environment: str,
    ) -> None:
        prior = {"canary": "test", "production": "canary"}.get(environment)
        if prior is None:
            return
        environment_value = self._environment(agent_name, prior)
        healthy = environment_value.get("healthySnapshotId")
        if not isinstance(healthy, str):
            raise PromotionError(
                f"{environment} promotion requires a healthy {prior} snapshot"
            )
        response = self._client.get(
            f"/v1/studio/agents/{agent_name}/deployment-snapshots",
            headers=self._headers,
        )
        if response.status_code >= 400:
            raise PromotionError(
                f"Harness API rejected snapshot lookup with status {response.status_code}"
            )
        try:
            raw_snapshots = response.json()
        except ValueError as error:
            raise PromotionError("snapshot lookup returned invalid JSON") from error
        if not isinstance(raw_snapshots, list):
            raise PromotionError("snapshot lookup returned a non-list response")
        snapshot: dict[str, object] | None = None
        for value in cast(list[object], raw_snapshots):
            item = _object(value, "snapshot lookup")
            if item.get("snapshotId") == healthy:
                snapshot = item
                break
        image = image_for(manifest, "sandbox")
        config = _object(snapshot.get("config"), "snapshot config") if snapshot else {}
        if snapshot is None or (
            snapshot.get("agentVersion") != agent_version
            or snapshot.get("packageHash") != package_hash
            or snapshot.get("imageDigest") != image.digest
            or config.get("RELEASE_ID") != manifest.release_id
        ):
            raise PromotionError(
                f"{prior} did not verify the exact signed release for {agent_name}"
            )

    def _wait(self, deployment_id: str) -> dict[str, object]:
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            view = self._json("GET", f"/v1/studio/deployments/{deployment_id}")
            deployment = _object(view.get("deployment"), "deployment")
            status = deployment.get("status")
            if status == "succeeded":
                return view
            if status == "failed":
                raise PromotionError(f"deployment failed: {deployment_id}")
            time.sleep(self._poll_seconds)
        raise PromotionError(f"deployment timed out: {deployment_id}")

    def promote(
        self,
        *,
        artifact_root: Path,
        manifest: ReleaseManifest,
        operation_id: str,
        environment: str,
        execution_profile: str,
        canary_percent: int,
        checkpoint: Callable[[PromotionPlan], None] | None = None,
    ) -> PromotionPlan:
        if environment not in {"test", "canary", "production"}:
            raise ValueError("environment must be test, canary, or production")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", operation_id) is None:
            raise ValueError("operation_id must be a safe identifier of at most 64 characters")
        self._publish(artifact_root, manifest)
        image = image_for(manifest, "sandbox")
        entries: list[PromotionEntry] = []
        try:
            for bundle in manifest.agent_bundles:
                self._require_prior_environment(
                    manifest=manifest,
                    agent_name=bundle.name,
                    agent_version=bundle.version,
                    package_hash=bundle.package_hash,
                    environment=environment,
                )
                self._gates(bundle.name, bundle.version)
                current = self._environment(bundle.name, environment)
                revision = current.get("revision")
                if not isinstance(revision, int) or isinstance(revision, bool):
                    raise PromotionError("environment revision is invalid")
                view = self._json(
                    "POST",
                    "/v1/studio/deployments/promote",
                    body={
                        "agentName": bundle.name,
                        "agentVersion": bundle.version,
                        "environment": environment,
                        "expectedEnvironmentRevision": revision,
                        "canaryPercent": canary_percent,
                        "imageDigest": image.digest,
                        "executionProfile": execution_profile,
                        "config": {"RELEASE_ID": manifest.release_id},
                        "idempotencyKey": _operation_key(
                            "promotion",
                            release_id=manifest.release_id,
                            operation_id=operation_id,
                            environment=environment,
                            agent_name=bundle.name,
                        ),
                    },
                )
                deployment = _object(view.get("deployment"), "deployment")
                deployment_id = deployment.get("deploymentId")
                previous = deployment.get("previousSnapshotId")
                target_id = deployment.get("targetSnapshotId")
                if not isinstance(deployment_id, str):
                    raise PromotionError("deployment response has no deploymentId")
                if previous is not None and not isinstance(previous, str):
                    raise PromotionError("deployment previous snapshot is invalid")
                if not isinstance(target_id, str):
                    raise PromotionError("deployment target snapshot is invalid")
                entries.append(
                    PromotionEntry(
                        agentName=bundle.name,
                        environment=environment,
                        previousSnapshotId=previous,
                        targetSnapshotId=target_id,
                        deploymentId=deployment_id,
                    )
                )
                partial = PromotionPlan(
                    releaseId=manifest.release_id,
                    operationId=operation_id,
                    environment=environment,
                    entries=tuple(entries),
                )
                if checkpoint is not None:
                    checkpoint(partial)
                completed = self._wait(deployment_id)
                target = _object(completed.get("target"), "deployment target")
                if target.get("imageDigest") != image.digest:
                    raise PromotionError("deployment changed the signed sandbox image digest")
                promoted_environment = self._environment(bundle.name, environment)
                if promoted_environment.get("healthySnapshotId") != target_id:
                    raise PromotionError(
                        "deployment succeeded without advancing the healthy environment snapshot"
                    )
        except Exception:
            if entries:
                partial = PromotionPlan(
                    releaseId=manifest.release_id,
                    operationId=operation_id,
                    environment=environment,
                    entries=tuple(entries),
                )
                try:
                    self.rollback(partial)
                except Exception as rollback_error:
                    raise PromotionError(
                        "promotion failed and automatic rollback did not complete"
                    ) from rollback_error
            raise
        return PromotionPlan(
            releaseId=manifest.release_id,
            operationId=operation_id,
            environment=environment,
            entries=tuple(entries),
        )

    def rollback(self, plan: PromotionPlan) -> PromotionPlan:
        for entry in reversed(plan.entries):
            if entry.previous_snapshot_id is None:
                continue
            current = self._environment(entry.agent_name, entry.environment)
            revision = current.get("revision")
            if not isinstance(revision, int) or isinstance(revision, bool):
                raise PromotionError("environment revision is invalid during rollback")
            view = self._json(
                "POST",
                (
                    f"/v1/studio/agents/{entry.agent_name}/environments/"
                    f"{entry.environment}/rollback"
                ),
                body={
                    "snapshotId": entry.previous_snapshot_id,
                    "expectedEnvironmentRevision": revision,
                    "idempotencyKey": _operation_key(
                        "rollback",
                        release_id=plan.release_id,
                        operation_id=plan.operation_id,
                        environment=entry.environment,
                        agent_name=entry.agent_name,
                        snapshot_id=entry.previous_snapshot_id,
                    ),
                },
            )
            deployment = _object(view.get("deployment"), "rollback deployment")
            deployment_id = deployment.get("deploymentId")
            if not isinstance(deployment_id, str):
                raise PromotionError("rollback response has no deploymentId")
            completed = self._wait(deployment_id)
            final_deployment = _object(completed.get("deployment"), "rollback deployment")
            target_id = final_deployment.get("targetSnapshotId")
            if target_id != entry.previous_snapshot_id:
                raise PromotionError("rollback did not restore the requested snapshot")
            restored_environment = self._environment(entry.agent_name, entry.environment)
            if restored_environment.get("healthySnapshotId") != entry.previous_snapshot_id:
                raise PromotionError(
                    "rollback succeeded without restoring the healthy environment snapshot"
                )
        # Keep the original recovery targets immutable. Re-running rollback with
        # the same plan therefore resolves to the same idempotent deployments
        # instead of accidentally promoting the failed targets again.
        return plan
