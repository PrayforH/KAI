"""Deterministic release manifest for build-once environment promotion."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harness.agent_package import extract_agent_bundle
from harness.core.manifest import load_manifest

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{7,64}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
REQUIRED_IMAGES = frozenset({"api", "web", "sandbox"})


class ReleaseModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class ReleaseAgentBundle(ReleaseModel):
    name: str
    version: str
    path: str
    archive_sha256: str = Field(alias="archiveSha256", pattern=r"^[a-f0-9]{64}$")
    manifest_hash: str = Field(alias="manifestHash", pattern=r"^[a-f0-9]{64}$")
    package_hash: str = Field(alias="packageHash", pattern=r"^[a-f0-9]{64}$")


class ReleaseImage(ReleaseModel):
    component: str
    reference: str
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    sbom_path: str = Field(alias="sbomPath")
    sbom_sha256: str = Field(alias="sbomSha256", pattern=r"^[a-f0-9]{64}$")


class ReleaseManifest(ReleaseModel):
    schema_version: str = Field(default="harness.release/v2", alias="schemaVersion")
    release_id: str = Field(alias="releaseId", pattern=r"^[a-f0-9]{64}$")
    platform_version: str | None = Field(
        default=None, alias="platformVersion", pattern=_VERSION.pattern
    )
    release_notes_path: str | None = Field(default=None, alias="releaseNotesPath")
    release_notes_sha256: str | None = Field(
        default=None, alias="releaseNotesSha256", pattern=r"^[a-f0-9]{64}$"
    )
    source_commit: str = Field(alias="sourceCommit", pattern=r"^[a-f0-9]{7,64}$")
    agent_bundles: tuple[ReleaseAgentBundle, ...] = Field(alias="agentBundles")
    images: tuple[ReleaseImage, ...]

    @model_validator(mode="after")
    def complete_and_unique(self) -> ReleaseManifest:
        if self.schema_version not in {"harness.release/v1", "harness.release/v2"}:
            raise ValueError(f"unsupported release schema: {self.schema_version}")
        if self.schema_version == "harness.release/v2" and (
            self.platform_version is None
            or self.release_notes_path is None
            or self.release_notes_sha256 is None
        ):
            raise ValueError(
                "harness.release/v2 requires platformVersion and signed release notes"
            )
        if self.schema_version == "harness.release/v1" and any(
            value is not None
            for value in (
                self.platform_version,
                self.release_notes_path,
                self.release_notes_sha256,
            )
        ):
            raise ValueError("harness.release/v1 cannot contain v2 release metadata")
        components = [image.component for image in self.images]
        if frozenset(components) != REQUIRED_IMAGES or len(components) != len(
            REQUIRED_IMAGES
        ):
            raise ValueError("release must contain exactly api, web, and sandbox images")
        identities = [(bundle.name, bundle.version) for bundle in self.agent_bundles]
        if len(set(identities)) != len(identities):
            raise ValueError("release contains duplicate Agent identities")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_payload(manifest: ReleaseManifest) -> bytes:
    payload = manifest.model_dump(
        mode="json", by_alias=True, exclude={"release_id"}, exclude_none=True
    )
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _safe_relative(root: Path, path: Path) -> str:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise ValueError(f"release artifact is outside its root: {path}")
    return resolved.relative_to(resolved_root).as_posix()


def _validate_sbom(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"SBOM is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"SBOM must be SPDX JSON or CycloneDX JSON: {path}")
    document = cast(dict[str, object], value)
    if not (
        isinstance(document.get("spdxVersion"), str)
        or document.get("bomFormat") == "CycloneDX"
    ):
        raise ValueError(f"SBOM must be SPDX JSON or CycloneDX JSON: {path}")


def _bundle(root: Path, archive: Path) -> ReleaseAgentBundle:
    with tempfile.TemporaryDirectory(prefix="harness-release-bundle-") as directory:
        manifest_path, manifest_hash, package_hash = extract_agent_bundle(
            archive.read_bytes(), destination=directory
        )
        metadata = load_manifest(manifest_path, environment="production").manifest.metadata
    return ReleaseAgentBundle(
        name=metadata.name,
        version=metadata.version,
        path=_safe_relative(root, archive),
        archiveSha256=_sha256(archive),
        manifestHash=manifest_hash,
        packageHash=package_hash,
    )


def create_release_manifest(
    *,
    artifact_root: Path,
    platform_version: str,
    release_notes_path: Path,
    source_commit: str,
    bundle_paths: Iterable[Path],
    image_references: Mapping[str, str],
    sbom_paths: Mapping[str, Path],
) -> ReleaseManifest:
    if _VERSION.fullmatch(platform_version) is None:
        raise ValueError("platform version must be SemVer")
    if _COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source commit must be a lowercase Git commit hash")
    root = artifact_root.resolve()
    release_notes_relative = _safe_relative(root, release_notes_path)
    if not release_notes_path.read_text(encoding="utf-8").strip():
        raise ValueError("release notes cannot be empty")
    bundles = tuple(
        sorted(
            (_bundle(root, path) for path in bundle_paths),
            key=lambda item: (item.name, item.version),
        )
    )
    if not bundles:
        raise ValueError("release must contain at least one Agent bundle")
    required = set(REQUIRED_IMAGES)
    if set(image_references) != required or set(sbom_paths) != required:
        raise ValueError("api, web, and sandbox image/SBOM inputs are required")
    images: list[ReleaseImage] = []
    for component in sorted(REQUIRED_IMAGES):
        reference_with_digest = image_references[component]
        reference, separator, digest = reference_with_digest.rpartition("@")
        if not separator or not reference or _IMAGE_DIGEST.fullmatch(digest) is None:
            raise ValueError(f"{component} image must be pinned by sha256 digest")
        sbom = sbom_paths[component]
        _validate_sbom(sbom)
        images.append(
            ReleaseImage(
                component=component,
                reference=reference,
                digest=digest,
                sbomPath=_safe_relative(root, sbom),
                sbomSha256=_sha256(sbom),
            )
        )
    provisional = ReleaseManifest(
        releaseId="0" * 64,
        platformVersion=platform_version,
        releaseNotesPath=release_notes_relative,
        releaseNotesSha256=_sha256(release_notes_path),
        sourceCommit=source_commit,
        agentBundles=bundles,
        images=tuple(images),
    )
    return provisional.model_copy(
        update={"release_id": hashlib.sha256(_canonical_payload(provisional)).hexdigest()}
    )


def write_release_manifest(manifest: ReleaseManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json", by_alias=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def load_release_manifest(path: Path) -> ReleaseManifest:
    try:
        manifest = ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"invalid release manifest: {path}") from error
    expected = hashlib.sha256(_canonical_payload(manifest)).hexdigest()
    if manifest.release_id != expected:
        raise ValueError("releaseId does not match the canonical manifest payload")
    return manifest


def verify_release_manifest(
    manifest: ReleaseManifest,
    *,
    artifact_root: Path,
    expected_commit: str | None = None,
    required_schema_version: str | None = None,
) -> None:
    if (
        required_schema_version is not None
        and manifest.schema_version != required_schema_version
    ):
        raise ValueError(
            f"release schema {manifest.schema_version} does not match required "
            f"{required_schema_version}"
        )
    if expected_commit is not None and manifest.source_commit != expected_commit:
        raise ValueError("release source commit does not match the requested promotion")
    root = artifact_root.resolve()
    if manifest.release_notes_path is not None:
        release_notes = root / manifest.release_notes_path
        if _safe_relative(root, release_notes) != manifest.release_notes_path:
            raise ValueError("release notes path verification failed")
        if _sha256(release_notes) != manifest.release_notes_sha256:
            raise ValueError("release notes verification failed")
    for expected in manifest.agent_bundles:
        actual = _bundle(root, root / expected.path)
        if actual != expected:
            raise ValueError(f"Agent bundle verification failed: {expected.path}")
    for image in manifest.images:
        sbom = root / image.sbom_path
        _validate_sbom(sbom)
        if _sha256(sbom) != image.sbom_sha256:
            raise ValueError(f"SBOM verification failed: {image.sbom_path}")


def image_for(manifest: ReleaseManifest, component: str) -> ReleaseImage:
    return next(image for image in manifest.images if image.component == component)
