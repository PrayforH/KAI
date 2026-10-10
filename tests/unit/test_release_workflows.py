from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml


def workflow(name: str) -> str:
    path = Path(".github/workflows") / name
    text = path.read_text(encoding="utf-8")
    assert isinstance(yaml.safe_load(text), dict)
    return text


def test_external_actions_are_pinned_to_full_commit_hashes() -> None:
    for name in ("verify.yml", "release.yml", "promote.yml"):
        for reference in re.findall(
            r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", workflow(name), re.MULTILINE
        ):
            if reference.startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[a-f0-9]{40}", reference), reference


def test_ci_does_not_depend_on_removed_minio_binary_distribution() -> None:
    verify = workflow("verify.yml")
    assert re.search(r"MINIO_SOURCE_COMMIT: [a-f0-9]{40}", verify)
    assert 'rev-parse HEAD)\" = \"$MINIO_SOURCE_COMMIT\"' in verify
    assert "CGO_ENABLED=0 go build" in verify
    assert 'client.make_bucket("harness-artifacts")' in verify
    assert "minio/minio:RELEASE" not in verify
    assert "minio/mc:RELEASE" not in verify


def test_ci_and_web_image_use_the_same_pinned_npm_version() -> None:
    dockerfile = Path("deploy/docker/web.Dockerfile").read_text()
    version = re.search(r"^ARG NPM_VERSION=(\S+)$", dockerfile, re.MULTILINE)
    assert version is not None
    assert f"npm install --global npm@{version.group(1)}" in workflow("verify.yml")


def test_ci_blocks_package_eval_migration_and_vulnerability_failures() -> None:
    verify = workflow("verify.yml")

    for required in (
        "make verify",
        "scripts/e2e_fake_runtime.py",
        "tests/unit/evals",
        "alembic downgrade -1",
        "--severity HIGH,CRITICAL --exit-code 1",
        "cosign verify",
        "--pull",
        "--vex /workspace/security/vex/cryptography-49.0.0.openvex.json",
    ):
        assert required in verify
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "verify: lint typecheck agent-check agent-determinism readiness test" in makefile


@pytest.mark.parametrize("failed_target", ["", "harness-api:ci", "harness-web:ci",
                                          "harness-sandbox:ci", "fs"])
def test_security_scans_every_target_and_preserves_failure(
    tmp_path: Path, failed_target: str
) -> None:
    data = cast(dict[str, Any], yaml.safe_load(workflow("verify.yml")))
    steps = data["jobs"]["container-security"]["steps"]
    step = next(s for s in steps if s.get("name", "").startswith("Verify the scanner"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, script in {
        "cosign": "#!/bin/bash\nexit 0\n",
        "docker": (
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$SCAN_LOG"\n'
            'if [[ -n "$FAILED_TARGET" && "$*" == *"$FAILED_TARGET"* ]]; '
            "then exit 1; fi\n"
        ),
    }.items():
        command = bin_dir / name
        command.write_text(script)
        command.chmod(0o755)
    scan_log = tmp_path / "scan.log"
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
             "RUNNER_TEMP": str(tmp_path), "TRIVY_IMAGE": "scanner:test",
             "SCAN_LOG": str(scan_log), "FAILED_TARGET": failed_target},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if failed_target else 0), result.stderr
    scans = scan_log.read_text().splitlines()
    assert len(scans) == 4
    for image in ("harness-api:ci", "harness-web:ci", "harness-sandbox:ci"):
        assert any(line.endswith(image) for line in scans)
    assert " fs " in scans[-1]


def test_release_builds_once_and_emits_signed_hash_and_sbom_evidence() -> None:
    release = workflow("release.yml")

    assert release.count("uses: docker/build-push-action@") == 3
    assert release.count("uses: anchore/sbom-action@") == 3
    assert "provenance: mode=max" in release
    assert "scripts/release_manifest.py create" in release
    assert "scripts/check_release_version.py" in release
    assert "--expected \"$version\" --require-released" in release
    assert '--platform-version "$RELEASE_VERSION"' in release
    assert "scripts/release_notes.py" in release
    assert '--release-notes "$PWD/dist/release/RELEASE_NOTES.md"' in release
    assert 'tag_version="${GITHUB_REF_NAME#v}"' in release
    assert "vars.HARNESS_RELEASE_RUNNER || 'ubuntu-latest'" in release
    assert "vars.HARNESS_RELEASE_REGISTRY || 'ghcr.io'" in release
    assert "vars.HARNESS_RELEASE_NAMESPACE || github.repository" in release
    assert "secrets.HARNESS_RELEASE_REGISTRY_USERNAME || github.actor" in release
    assert "secrets.HARNESS_RELEASE_REGISTRY_PASSWORD || secrets.GITHUB_TOKEN" in release
    assert "publish-draft-release:" in release
    assert "--verify-tag --draft" in release
    assert "agent-studio-$RELEASE_VERSION-evidence.tar.gz" in release
    assert '--bundle "$evidence.sigstore.json"' in release
    assert "cosign attest" in release
    assert "cosign sign-blob" in release
    assert '--bundle "dist/release/sbom/$component.sigstore.json"' in release
    assert "--type openvex" in release
    assert "cryptography-49.0.0.sigstore.json" in release
    assert "pull: true" in release
    assert "--bundle dist/release/release-manifest.sigstore.json" in release
    assert "release-${{ github.sha }}" in release


def test_promotion_never_rebuilds_and_has_stop_and_rollback_gates() -> None:
    promote = workflow("promote.yml")

    assert re.search(r"docker build(?:\s|x build\s)", promote) is None
    assert "build-push-action" not in promote
    assert "scripts/release_manifest.py verify" in promote
    assert "uses: docker/login-action@" in promote
    assert "vars.HARNESS_RELEASE_REGISTRY || 'ghcr.io'" in promote
    assert "secrets.HARNESS_RELEASE_REGISTRY_USERNAME || github.actor" in promote
    assert "secrets.HARNESS_RELEASE_REGISTRY_PASSWORD || secrets.GITHUB_TOKEN" in promote
    assert "--required-schema-version harness.release/v2" in promote
    assert "cosign verify-attestation" in promote
    assert '--bundle "dist/release/sbom/$component.sigstore.json"' in promote
    assert "--type openvex" in promote
    assert "cryptography-49.0.0.sigstore.json" in promote
    assert "docker buildx imagetools inspect" in promote
    assert 'args["vcs:revision"]' in promote
    assert ") == $expected" in promote
    assert "scripts/deploy_release.py apply" in promote
    assert "scripts/promote_release.py promote" in promote
    assert '--operation-id "$PROMOTION_OPERATION_ID"' in promote
    assert "github.run_id }}-${{ github.run_attempt" in promote
    assert "scripts/benchmark_chat_latency.py" in promote
    assert "--skip-publish" in promote
    assert "--require-text" in promote
    assert "any(.agentBundles[]; .name == $name and .version == $version)" in promote
    assert "scripts/promote_release.py rollback" in promote
    assert promote.count('--user "$HARNESS_RELEASE_USER_ID"') == 2
    assert "HARNESS_RELEASE_USER_ID: ${{ vars.HARNESS_SMOKE_USER_ID }}" in promote
    assert "scripts/deploy_release.py rollback" in promote
    assert "if: always()" in promote
    assert "promotion-evidence-${{ inputs.environment }}" in promote
    assert "cancel-in-progress: false" in promote
    assert "environment: ${{ inputs.environment }}" in promote
    assert "publish-production-release:" in promote
    assert "if: inputs.environment == 'production'" in promote
    assert "Require an exact signed tag draft before any production mutation" in promote
    assert promote.index("Require an exact signed tag draft") < promote.index(
        "Deploy exact image digests"
    )
    assert promote.count('test "$(git rev-list -n 1 "$tag")" = "$EXPECTED_COMMIT"') == 2
    assert "fetch-depth: 0" in promote
    assert 'gh release edit "$tag"' in promote
    assert "--draft=false --latest" in promote


def test_release_compose_overlay_contains_only_digest_injected_images() -> None:
    overlay = Path("deploy/docker-compose/compose.release.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(overlay)

    assert isinstance(parsed, dict)
    assert "build:" not in overlay
    assert "HARNESS_API_IMAGE" in overlay
    assert "HARNESS_WEB_IMAGE" in overlay
    document = cast(dict[str, object], parsed)
    services = cast(dict[str, dict[str, str]], document["services"])
    assert services["migrate"]["image"] == services["seed"]["image"]
