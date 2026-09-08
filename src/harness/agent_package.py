"""Production readiness checks and reproducible Agent package bundles."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from harness.core.manifest import AgentManifestSnapshot, load_manifest
from harness.evals.suite import EvalSuite, EvalSuiteValidationError, load_eval_suite
from harness.policy.profiles import default_policy_profiles

Environment = Literal["local", "test", "production"]


@dataclass(frozen=True)
class AgentPackageReport:
    snapshot: AgentManifestSnapshot
    eval_suite: EvalSuite
    package_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "agent": self.snapshot.manifest.metadata.name,
            "version": self.snapshot.manifest.metadata.version,
            "content_hash": self.snapshot.content_hash,
            "package_hash": self.package_hash,
            "skill_count": len(self.snapshot.skill_snapshots),
            "evaluation_case_count": len(self.eval_suite.cases),
            "status": "ready",
        }


class AgentPackageCheckError(ValueError):
    """Raised when a domain Agent package is not production ready."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("production package check failed: " + "; ".join(issues))


class AgentBundleValidationError(ValueError):
    """Raised when an uploaded release bundle is unsafe or inconsistent."""


_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_REQUIRED_PROMPT_HEADINGS = (
    "## Mission",
    "## Operating workflow",
    "## Evidence and tool use",
    "## Safety boundaries",
    "## Output contract",
)
_SECRET_FILE = re.compile(
    r"(^|/)(?:\.env(?:\..+)?|credentials?(?:\..+)?|secrets?(?:\..+)?|"
    r"id_rsa|id_ed25519|[^/]+\.(?:pem|key|p12|pfx))$",
    re.IGNORECASE,
)
_TEXT_FILE_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}
_SECRET_CONTENT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-(?:lf-|live-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bdtn_[A-Fa-f0-9]{32,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{20,}\b"),
)
_EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv"}
_MAX_PACKAGE_FILE_BYTES = 64 * 1024 * 1024
_MAX_PACKAGE_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_BUNDLE_FILES = 20_000
_MAX_BUNDLE_PATH_CHARS = 512
_MAX_BUNDLE_PATH_COMPONENT_CHARS = 255
MAX_AGENT_BUNDLE_UPLOAD_BYTES = _MAX_PACKAGE_TOTAL_BYTES


def _path_is_too_long(relative: PurePosixPath) -> bool:
    return len(relative.as_posix()) > _MAX_BUNDLE_PATH_CHARS or any(
        len(part) > _MAX_BUNDLE_PATH_COMPONENT_CHARS for part in relative.parts
    )


def _contains_secret_content(content: bytes) -> bool:
    text = content.decode("utf-8", errors="ignore")
    return any(pattern.search(text) for pattern in _SECRET_CONTENT_PATTERNS)


def _package_files(root: Path) -> list[Path]:
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        relative_text = relative.as_posix()
        if _path_is_too_long(PurePosixPath(relative_text)):
            raise AgentPackageCheckError(
                [f"package path exceeds the length limit: {relative_text}"]
            )
        if relative_text == "bundle.json":
            raise AgentPackageCheckError(["bundle.json is a reserved release path"])
        if path.is_symlink():
            raise AgentPackageCheckError([f"package contains symlink: {relative_text}"])
        if not path.is_file() or path.name == ".DS_Store":
            continue
        if _SECRET_FILE.search(relative_text):
            raise AgentPackageCheckError(
                [f"package contains a secret-like file: {relative_text}"]
            )
        size = path.stat().st_size
        if size > _MAX_PACKAGE_FILE_BYTES:
            raise AgentPackageCheckError(
                [f"package file exceeds {_MAX_PACKAGE_FILE_BYTES} bytes: {relative_text}"]
            )
        total += size
        if total > _MAX_PACKAGE_TOTAL_BYTES:
            raise AgentPackageCheckError(
                [f"package exceeds {_MAX_PACKAGE_TOTAL_BYTES} total bytes"]
            )
        content = path.read_bytes()
        if path.suffix.lower() in _TEXT_FILE_SUFFIXES:
            try:
                content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise AgentPackageCheckError(
                    [f"text package file is not UTF-8: {relative_text}"]
                ) from error
        if _contains_secret_content(content):
            raise AgentPackageCheckError(
                [f"package contains secret-like content: {relative_text}"]
            )
        files.append(path)
        if len(files) >= _MAX_BUNDLE_FILES:
            raise AgentPackageCheckError(
                [f"package must contain fewer than {_MAX_BUNDLE_FILES} files"]
            )
    return files


def _package_hash(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def check_agent_package(
    manifest_path: str | Path,
    *,
    environment: Environment = "production",
) -> AgentPackageReport:
    manifest = Path(manifest_path).resolve()
    snapshot = load_manifest(manifest, environment=environment)
    spec = snapshot.manifest.spec
    metadata = snapshot.manifest.metadata
    issues: list[str] = []

    if _SEMVER.fullmatch(metadata.version) is None:
        issues.append("metadata.version must be semantic versioning")
    domain = metadata.labels.get("domain", "").strip()
    if not domain or domain == "replace-me":
        issues.append("metadata.labels.domain must identify the business domain")
    if not {"streaming", "tool_use"}.issubset(spec.model.required_capabilities):
        issues.append("model must require streaming and tool_use capabilities")
    if (
        spec.tool_exposure_mode == "on_demand"
        and "tool_search" not in spec.model.required_capabilities
    ):
        issues.append("on-demand tool exposure requires the tool_search capability")
    if not spec.workspace.archive_on_complete:
        issues.append("workspace.archiveOnComplete must be enabled")
    if spec.permissions.policy not in default_policy_profiles().ids:
        issues.append(
            "permissions.policy must be a registered production policy profile"
        )
    write_tools = {
        tool.builtin
        for tool in spec.tools
        if tool.builtin in {"Write", "Edit", "Bash"}
    }
    if write_tools and spec.permissions.policy == "production-read-only":
        issues.append("write-capable tools cannot use production-read-only policy")
    if any(tool.builtin == "Task" for tool in spec.tools) and not spec.subagents:
        issues.append("Task tool requires at least one pinned subagent")

    prompt = snapshot.system_prompt
    for heading in _REQUIRED_PROMPT_HEADINGS:
        if heading not in prompt:
            issues.append(f"system prompt is missing required heading: {heading}")
    if "replace-me" in prompt.lower() or "TODO" in prompt:
        issues.append("system prompt contains an unfinished placeholder")

    eval_path = manifest.parent / "evals" / "suite.yaml"
    try:
        suite = load_eval_suite(eval_path, expected_agent=metadata.name)
    except EvalSuiteValidationError as error:
        issues.append(str(error))
        suite = EvalSuite.model_validate(
            {
                "apiVersion": "harness/v1alpha1",
                "kind": "EvalSuite",
                "agent": metadata.name,
                "cases": [
                    {
                        "id": "invalid-placeholder",
                        "tags": ["invalid"],
                        "prompt": "invalid",
                    }
                ],
            }
        )
    evaluation_enabled = (
        metadata.labels.get("evaluation-enabled", "true").strip().lower()
        != "false"
    )
    if evaluation_enabled:
        coverage = {tag for case in suite.cases for tag in case.tags}
        for required in ("happy", "ambiguous", "safety"):
            if required not in coverage:
                issues.append(f"evaluation suite is missing {required} coverage")
    for case in suite.cases:
        for fixture in case.input_files:
            fixture_path = (manifest.parent / fixture.path).resolve()
            if (
                not fixture_path.is_relative_to(manifest.parent)
                or not fixture_path.is_file()
            ):
                issues.append(
                    f"evaluation input does not exist for {case.id}: {fixture.path}"
                )

    files: list[Path] = []
    try:
        files = _package_files(manifest.parent)
    except AgentPackageCheckError as error:
        issues.extend(error.issues)

    if issues:
        raise AgentPackageCheckError(issues)
    return AgentPackageReport(
        snapshot=snapshot,
        eval_suite=suite,
        package_hash=_package_hash(manifest.parent, files),
    )


def pack_agent_package(
    manifest_path: str | Path,
    *,
    output_directory: str | Path,
) -> tuple[Path, AgentPackageReport]:
    manifest = Path(manifest_path).resolve()
    report = check_agent_package(manifest, environment="production")
    files = _package_files(manifest.parent)
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    metadata = report.snapshot.manifest.metadata
    archive = output / (
        f"{metadata.name}-{metadata.version}-{report.package_hash[:12]}.zip"
    )
    provenance_files = [
        {
            "path": path.relative_to(manifest.parent).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_bytes": path.stat().st_size,
        }
        for path in files
    ]
    provenance = json.dumps(
        {
            "apiVersion": "harness/v1alpha1",
            "kind": "AgentBundle",
            "agent": metadata.name,
            "version": metadata.version,
            "manifestContentHash": report.snapshot.content_hash,
            "packageContentHash": report.package_hash,
            "files": provenance_files,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as bundle:
        for relative, content in [
            *(
                (path.relative_to(manifest.parent).as_posix(), path.read_bytes())
                for path in files
            ),
            ("bundle.json", provenance),
        ]:
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            bundle.writestr(info, content)
    return archive, report


def extract_agent_bundle(
    content: bytes,
    *,
    destination: str | Path,
) -> tuple[Path, str, str]:
    """Safely extract a reproducible bundle and return its Manifest and hash claim."""

    if len(content) > MAX_AGENT_BUNDLE_UPLOAD_BYTES:
        raise AgentBundleValidationError("Agent bundle exceeds the upload size limit")
    target_root = Path(destination).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise AgentBundleValidationError("Agent bundle is not a valid ZIP archive") from error
    with archive:
        infos = archive.infolist()
        if len(infos) > _MAX_BUNDLE_FILES:
            raise AgentBundleValidationError("Agent bundle contains too many files")
        total = 0
        seen: set[str] = set()
        extracted_files: set[str] = set()
        for info in infos:
            if "\\" in info.filename or "\x00" in info.filename:
                raise AgentBundleValidationError("Agent bundle contains an unsafe path")
            relative = PurePosixPath(info.filename)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise AgentBundleValidationError(
                    f"Agent bundle contains an unsafe path: {info.filename}"
                )
            normalized = relative.as_posix()
            if _path_is_too_long(relative):
                raise AgentBundleValidationError(
                    f"Agent bundle path exceeds the length limit: {normalized}"
                )
            if normalized in seen:
                raise AgentBundleValidationError(
                    f"Agent bundle contains a duplicate path: {normalized}"
                )
            seen.add(normalized)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise AgentBundleValidationError(
                    f"Agent bundle contains a symlink: {normalized}"
                )
            if info.is_dir():
                continue
            extracted_files.add(normalized)
            if info.file_size > _MAX_PACKAGE_FILE_BYTES:
                raise AgentBundleValidationError(
                    f"Agent bundle file exceeds the size limit: {normalized}"
                )
            total += info.file_size
            if total > _MAX_PACKAGE_TOTAL_BYTES:
                raise AgentBundleValidationError(
                    "Agent bundle exceeds the uncompressed size limit"
                )
            data = archive.read(info)
            if len(data) != info.file_size:
                raise AgentBundleValidationError(
                    f"Agent bundle file size is inconsistent: {normalized}"
                )
            output = target_root.joinpath(*relative.parts)
            if not output.resolve().is_relative_to(target_root):
                raise AgentBundleValidationError(
                    f"Agent bundle path escaped the extraction root: {normalized}"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)

    manifest = target_root / "agent.yaml"
    provenance_path = target_root / "bundle.json"
    if not manifest.is_file() or not provenance_path.is_file():
        raise AgentBundleValidationError(
            "Agent bundle must contain agent.yaml and bundle.json at its root"
        )
    try:
        provenance_value = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentBundleValidationError("Agent bundle provenance is invalid") from error
    if not isinstance(provenance_value, dict):
        raise AgentBundleValidationError("Agent bundle provenance must be an object")
    provenance = cast(dict[str, object], provenance_value)
    claimed_hash = provenance.get("manifestContentHash")
    if not isinstance(claimed_hash, str) or re.fullmatch(r"[0-9a-f]{64}", claimed_hash) is None:
        raise AgentBundleValidationError("Agent bundle provenance has an invalid hash")
    claimed_package_hash = provenance.get("packageContentHash")
    if (
        not isinstance(claimed_package_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", claimed_package_hash) is None
    ):
        raise AgentBundleValidationError(
            "Agent bundle provenance has an invalid package hash"
        )
    raw_files = provenance.get("files")
    if not isinstance(raw_files, list):
        raise AgentBundleValidationError("Agent bundle provenance has no file inventory")
    expected_files: set[str] = set()
    for raw_file in cast(list[object], raw_files):
        if not isinstance(raw_file, dict):
            raise AgentBundleValidationError(
                "Agent bundle provenance contains an invalid file record"
            )
        record = cast(dict[str, object], raw_file)
        path = record.get("path")
        sha256 = record.get("sha256")
        size_bytes = record.get("size_bytes")
        if (
            not isinstance(path, str)
            or path == "bundle.json"
            or path in expected_files
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise AgentBundleValidationError(
                "Agent bundle provenance contains an invalid file record"
            )
        expected_files.add(path)
        if path not in extracted_files:
            raise AgentBundleValidationError(
                f"Agent bundle provenance references a missing file: {path}"
            )
        file_path = target_root.joinpath(*PurePosixPath(path).parts)
        if not file_path.is_file():
            raise AgentBundleValidationError(
                f"Agent bundle provenance references a missing file: {path}"
            )
        data = file_path.read_bytes()
        if len(data) != size_bytes or hashlib.sha256(data).hexdigest() != sha256:
            raise AgentBundleValidationError(
                f"Agent bundle file does not match provenance: {path}"
            )
    actual_files = extracted_files - {"bundle.json"}
    if actual_files != expected_files:
        unexpected = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        details = [
            *(f"unexpected {path}" for path in unexpected),
            *(f"missing {path}" for path in missing),
        ]
        raise AgentBundleValidationError(
            "Agent bundle file inventory does not match provenance: "
            + ", ".join(details)
        )
    package_files = [target_root / path for path in sorted(actual_files)]
    if _package_hash(target_root, package_files) != claimed_package_hash:
        raise AgentBundleValidationError(
            "Agent bundle package hash does not match its file inventory"
        )
    provenance_path.unlink()
    return manifest, claimed_hash, claimed_package_hash
