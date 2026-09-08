from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.agent_package import pack_agent_package
from harness.release import (
    ReleaseManifest,
    create_release_manifest,
    load_release_manifest,
    verify_release_manifest,
    write_release_manifest,
)
from scripts.verify_agent_determinism import verify


def _release_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "release"
    agents = root / "agents"
    agents.mkdir(parents=True)
    archive, _report = pack_agent_package(
        "agents/echo-agent/agent.yaml", output_directory=agents
    )
    sboms = root / "sbom"
    sboms.mkdir()
    for component in ("api", "web", "sandbox"):
        (sboms / f"{component}.spdx.json").write_text(
            json.dumps({"spdxVersion": "SPDX-2.3", "name": component}),
            encoding="utf-8",
        )
    (root / "RELEASE_NOTES.md").write_text(
        "## [0.1.0]\n\n### Added\n\n- Test release.\n", encoding="utf-8"
    )
    return root, archive


def _manifest(root: Path, archive: Path) -> ReleaseManifest:
    return create_release_manifest(
        artifact_root=root,
        platform_version="0.1.0",
        release_notes_path=root / "RELEASE_NOTES.md",
        source_commit="a" * 40,
        bundle_paths=(archive,),
        image_references={
            component: f"ghcr.io/example/harness-{component}@sha256:{index * 64}"
            for component, index in (("api", "a"), ("web", "b"), ("sandbox", "c"))
        },
        sbom_paths={
            component: root / "sbom" / f"{component}.spdx.json"
            for component in ("api", "web", "sandbox")
        },
    )


def test_release_manifest_is_deterministic_and_verifies_all_artifacts(
    tmp_path: Path,
) -> None:
    root, archive = _release_root(tmp_path)
    first = _manifest(root, archive)
    second = _manifest(root, archive)
    path = root / "release-manifest.json"

    write_release_manifest(first, path)
    loaded = load_release_manifest(path)
    verify_release_manifest(loaded, artifact_root=root, expected_commit="a" * 40)

    assert first == second == loaded
    assert loaded.schema_version == "harness.release/v2"
    assert loaded.platform_version == "0.1.0"
    assert loaded.release_notes_path == "RELEASE_NOTES.md"
    assert loaded.agent_bundles[0].name == "echo-agent"
    assert {image.component for image in loaded.images} == {"api", "web", "sandbox"}


def test_release_manifest_rejects_changed_bundle_and_sbom(tmp_path: Path) -> None:
    root, archive = _release_root(tmp_path)
    manifest = _manifest(root, archive)
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(ValueError):
        verify_release_manifest(manifest, artifact_root=root)


def test_v1_manifest_remains_loadable_for_rollback_but_cannot_be_promoted(
    tmp_path: Path,
) -> None:
    root, archive = _release_root(tmp_path)
    current = _manifest(root, archive)
    payload = current.model_dump(mode="json", by_alias=True, exclude={"release_id"})
    payload["schemaVersion"] = "harness.release/v1"
    payload.pop("platformVersion")
    payload.pop("releaseNotesPath")
    payload.pop("releaseNotesSha256")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["releaseId"] = hashlib.sha256(canonical).hexdigest()
    path = root / "legacy-release-manifest.json"
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    legacy = load_release_manifest(path)
    verify_release_manifest(legacy, artifact_root=root)
    assert legacy.schema_version == "harness.release/v1"
    assert legacy.platform_version is None
    assert legacy.release_notes_path is None
    with pytest.raises(ValueError, match="does not match required"):
        verify_release_manifest(
            legacy,
            artifact_root=root,
            required_schema_version="harness.release/v2",
        )

    root, archive = _release_root(tmp_path / "sbom-case")
    manifest = _manifest(root, archive)
    (root / "sbom/api.spdx.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="SBOM"):
        verify_release_manifest(manifest, artifact_root=root)

    root, archive = _release_root(tmp_path / "notes-case")
    manifest = _manifest(root, archive)
    (root / "RELEASE_NOTES.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="release notes"):
        verify_release_manifest(manifest, artifact_root=root)


def test_every_reference_agent_bundle_is_byte_deterministic() -> None:
    hashes = verify(Path("agents"))

    assert len(hashes) == 9
