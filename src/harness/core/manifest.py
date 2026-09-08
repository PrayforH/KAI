"""Agent Manifest schema, validation and deterministic snapshotting."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ManifestValidationError(ValueError):
    """Raised when an Agent Manifest cannot be safely published."""


class ManifestModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class AgentMetadata(ManifestModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)


class ModelSpec(ManifestModel):
    route: str = Field(min_length=1)
    model: str = Field(min_length=1)
    fallback_route: str | None = Field(default=None, alias="fallbackRoute")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    required_capabilities: tuple[str, ...] = Field(
        default_factory=tuple, alias="requiredCapabilities"
    )


class PromptSpec(ManifestModel):
    system: str = Field(min_length=1)


class ToolSpec(ManifestModel):
    builtin: str | None = None
    python_entry: str | None = Field(default=None, alias="python")
    mcp: str | None = None

    @model_validator(mode="after")
    def exactly_one_tool_source(self) -> ToolSpec:
        values = (self.builtin, self.python_entry, self.mcp)
        if sum(value is not None for value in values) != 1:
            raise ValueError("tool must declare exactly one of builtin, python, or mcp")
        return self


ToolExposureMode = Literal["eager", "on_demand"]


class ToolDirectoryEntry(ManifestModel):
    name: str = Field(min_length=1)
    source: Literal["builtin", "mcp", "python"]
    logical_reference: str = Field(alias="logicalReference", min_length=1)
    description: str = Field(min_length=1, max_length=2_000)
    risk: Literal["low", "medium", "high"]
    result_trust: Literal["safe", "sensitive", "untrusted"] = Field(alias="resultTrust")


class ToolDirectorySnapshot(ManifestModel):
    schema_version: Literal["harness.tool-directory/v1"] = Field(alias="schemaVersion")
    catalog_revision: int = Field(alias="catalogRevision", ge=1)
    exposure_mode: ToolExposureMode = Field(alias="exposureMode")
    entries: tuple[ToolDirectoryEntry, ...]
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")

    def digest(self) -> str:
        payload = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"content_hash"},
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        catalog_revision: int,
        exposure_mode: ToolExposureMode,
        entries: Sequence[ToolDirectoryEntry],
    ) -> ToolDirectorySnapshot:
        ordered = tuple(sorted(entries, key=lambda item: (item.source, item.name)))
        payload = {
            "schemaVersion": "harness.tool-directory/v1",
            "catalogRevision": catalog_revision,
            "exposureMode": exposure_mode,
            "entries": [item.model_dump(mode="json", by_alias=True) for item in ordered],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return cls.model_validate(
            {
                **payload,
                "contentHash": hashlib.sha256(canonical.encode()).hexdigest(),
            }
        )

    @model_validator(mode="after")
    def valid_directory(self) -> ToolDirectorySnapshot:
        names = [item.name for item in self.entries]
        if len(names) != len(set(names)):
            raise ValueError("tool directory entries must have unique names")
        if self.content_hash != self.digest():
            raise ValueError("tool directory content hash does not match its entries")
        return self


class SubagentSpec(ManifestModel):
    ref: str = Field(min_length=1)
    alias: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    description: str | None = Field(default=None, min_length=1, max_length=500)
    background: bool = False

    @property
    def runtime_name(self) -> str:
        package_name, separator, _version = self.ref.rpartition("@")
        return self.alias or (package_name if separator else self.ref)


class HookSpec(ManifestModel):
    python_entry: str = Field(alias="python", min_length=1)


class PermissionSpec(ManifestModel):
    policy: str = Field(min_length=1)


class WorkspaceSpec(ManifestModel):
    mode: Literal["isolated"] = "isolated"
    restore_session: bool = Field(default=True, alias="restoreSession")
    archive_on_complete: bool = Field(default=True, alias="archiveOnComplete")


class LimitSpec(ManifestModel):
    max_turns: int | None = Field(default=None, alias="maxTurns", ge=1)
    # Codex can issue several tool calls inside one model turn. Keep this
    # independent from ``maxTurns`` so long agentic loops are not terminated
    # merely because a single turn performs substantial workspace work.
    max_tool_calls: int | None = Field(default=256, alias="maxToolCalls", ge=1, le=4096)
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds", ge=1)
    max_budget_usd: float | None = Field(default=None, alias="maxBudgetUsd", gt=0)
    max_model_tokens: int | None = Field(default=None, alias="maxModelTokens", ge=1)
    max_subagents: int = Field(default=8, alias="maxSubagents", ge=1, le=32)
    max_subagent_tasks: int = Field(default=16, alias="maxSubagentTasks", ge=1, le=128)
    max_concurrent_subagents: int = Field(default=4, alias="maxConcurrentSubagents", ge=1, le=16)
    max_subagent_depth: Literal[1] = Field(default=1, alias="maxSubagentDepth")
    max_subagent_usage_units: int | None = Field(
        default=None, alias="maxSubagentUsageUnits", gt=0
    )


class AgentSpec(ManifestModel):
    runtime: Literal["claude-agent-sdk", "codex-app-server"]
    model: ModelSpec
    prompt: PromptSpec
    skills: tuple[str, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    tool_exposure_mode: ToolExposureMode = Field(
        default="eager",
        alias="toolExposureMode",
    )
    knowledge_references: tuple[str, ...] = Field(
        default=(),
        alias="knowledgeReferences",
    )
    subagents: tuple[SubagentSpec, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    permissions: PermissionSpec
    workspace: WorkspaceSpec = WorkspaceSpec()
    limits: LimitSpec = LimitSpec()

    @model_validator(mode="after")
    def unique_subagent_runtime_names(self) -> AgentSpec:
        runtime_names = [subagent.runtime_name for subagent in self.subagents]
        duplicates = sorted({name for name in runtime_names if runtime_names.count(name) > 1})
        if duplicates:
            raise ValueError("duplicate subagent runtime name: " + ", ".join(duplicates))
        if len(runtime_names) > self.limits.max_subagents:
            raise ValueError(f"declared subagents exceed maxSubagents={self.limits.max_subagents}")
        if len(set(self.knowledge_references)) != len(self.knowledge_references):
            raise ValueError("duplicate Knowledge Base reference")
        if any(
            not re.fullmatch(r"[a-z][a-z0-9-]*", reference)
            for reference in self.knowledge_references
        ):
            raise ValueError("invalid Knowledge Base reference")
        return self


class AgentManifest(ManifestModel):
    api_version: Literal["harness/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["Agent"]
    metadata: AgentMetadata
    spec: AgentSpec


class SkillFileSnapshot(ManifestModel):
    path: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class SkillSnapshot(ManifestModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)
    files: tuple[SkillFileSnapshot, ...] = ()
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class PythonToolSnapshot(ManifestModel):
    """Immutable, self-contained Python operator executed in the Run sandbox."""

    reference: str = Field(min_length=1)
    path: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=2_000)
    input_schema: dict[str, Any] = Field(alias="inputSchema")
    content_base64: str = Field(alias="contentBase64", min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(alias="sizeBytes", ge=1)


class AgentManifestSnapshot(ManifestModel):
    manifest: AgentManifest
    system_prompt: str
    skill_snapshots: tuple[SkillSnapshot, ...] = ()
    python_tool_snapshots: tuple[PythonToolSnapshot, ...] = Field(
        default=(), alias="pythonToolSnapshots"
    )
    tool_directory: ToolDirectorySnapshot | None = None
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


_SECRET_KEY = re.compile(r"(?:api[_-]?key|token|password|secret)", re.IGNORECASE)
_NON_SECRET_TOKEN_FIELDS = {"maxmodeltokens"}
_MAX_SKILL_FILE_BYTES = 64 * 1024 * 1024
_MAX_SKILL_TOTAL_BYTES = 256 * 1024 * 1024
TOOL_DIRECTORY_FILENAME = "tool-directory.json"
_MAX_TOOL_DIRECTORY_BYTES = 2 * 1024 * 1024
_MAX_PYTHON_TOOL_BYTES = 1024 * 1024


def _assert_no_inline_secrets(value: object, path: str = "manifest") -> None:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        for key, child in mapping.items():
            key_text = str(key)
            if (
                _SECRET_KEY.search(key_text)
                and key_text.lower() not in _NON_SECRET_TOKEN_FIELDS
                and child not in (None, "")
            ):
                raise ManifestValidationError(
                    f"secret-like field is not allowed: {path}.{key_text}"
                )
            _assert_no_inline_secrets(child, f"{path}.{key_text}")
    elif isinstance(value, list):
        sequence = cast(list[object], value)
        for index, child in enumerate(sequence):
            _assert_no_inline_secrets(child, f"{path}[{index}]")


def _resolve_file(root: Path, relative: str, label: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ManifestValidationError(f"{label} must stay inside the Agent directory")
    if not candidate.is_file():
        raise ManifestValidationError(f"{label} file does not exist: {relative}")
    return candidate


def _parse_skill_frontmatter(path: Path) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ManifestValidationError(f"cannot read Skill frontmatter: {error}") from error
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ManifestValidationError("SKILL.md must start with YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise ManifestValidationError("SKILL.md frontmatter is not closed") from error
    try:
        raw = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        raise ManifestValidationError(f"invalid SKILL.md frontmatter: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestValidationError("SKILL.md frontmatter must be a YAML object")
    frontmatter = cast(dict[str, object], raw)
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9-]*", name) is None:
        raise ManifestValidationError("Skill name must be lowercase kebab-case")
    if not isinstance(description, str) or not description.strip():
        raise ManifestValidationError("Skill description must be a non-empty string")
    return name, description.strip()


def _snapshot_skill(root: Path, relative: str) -> SkillSnapshot:
    skill_path = (root / relative).resolve()
    if not skill_path.is_relative_to(root) or not skill_path.exists():
        raise ManifestValidationError(f"skill path does not exist: {relative}")
    if not skill_path.is_dir():
        raise ManifestValidationError(f"skill path must be a directory: {relative}")
    source_path = root / relative
    if source_path.is_symlink():
        raise ManifestValidationError(f"Skill path cannot be a symlink: {relative}")
    paths = sorted(path for path in skill_path.rglob("*") if path.is_file() or path.is_symlink())
    if not paths:
        raise ManifestValidationError(f"Skill directory is empty: {relative}")
    for path in paths:
        if path.is_symlink():
            raise ManifestValidationError(
                f"Skill files cannot be symlinks: {path.relative_to(skill_path).as_posix()}"
            )
        if not path.is_file():
            raise ManifestValidationError(
                f"Skill contains an unsupported file: {path.relative_to(skill_path).as_posix()}"
            )
    skill_md = skill_path / "SKILL.md"
    if not skill_md.is_file():
        raise ManifestValidationError(f"Skill directory must contain SKILL.md: {relative}")
    name, description = _parse_skill_frontmatter(skill_md)
    if skill_path.name != name:
        raise ManifestValidationError(
            f"Skill directory name must match frontmatter name: {skill_path.name} != {name}"
        )
    skill_digest = hashlib.sha256()
    snapshots: list[SkillFileSnapshot] = []
    total_bytes = 0
    for path in paths:
        content = path.read_bytes()
        if len(content) > _MAX_SKILL_FILE_BYTES:
            raise ManifestValidationError(
                f"Skill file exceeds {_MAX_SKILL_FILE_BYTES} bytes: "
                f"{path.relative_to(skill_path).as_posix()}"
            )
        total_bytes += len(content)
        if total_bytes > _MAX_SKILL_TOTAL_BYTES:
            raise ManifestValidationError(
                f"Skill exceeds {_MAX_SKILL_TOTAL_BYTES} total bytes: {relative}"
            )
        relative_file = path.relative_to(skill_path).as_posix()
        file_hash = hashlib.sha256(content).hexdigest()
        skill_digest.update(relative_file.encode())
        skill_digest.update(content)
        snapshots.append(
            SkillFileSnapshot(
                path=relative_file,
                content_base64=base64.b64encode(content).decode("ascii"),
                sha256=file_hash,
                size_bytes=len(content),
            )
        )
    return SkillSnapshot(
        name=name,
        description=description,
        source=relative,
        files=tuple(snapshots),
        content_hash=skill_digest.hexdigest(),
    )


def _snapshot_python_tool(root: Path, reference: str) -> PythonToolSnapshot:
    prefix = "bundle:"
    if not reference.startswith(prefix):
        raise ManifestValidationError(
            f"Python tool is not a self-contained Bundle reference: {reference}"
        )
    relative_text = reference.removeprefix(prefix)
    relative = PurePosixPath(relative_text)
    if (
        not relative.parts
        or relative.is_absolute()
        or "\\" in relative_text
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix != ".py"
    ):
        raise ManifestValidationError(f"unsafe Bundle Python tool path: {relative_text}")
    path = _resolve_file(root, relative.as_posix(), "Bundle Python tool")
    if path.is_symlink():
        raise ManifestValidationError("Bundle Python tool cannot be a symlink")
    content = path.read_bytes()
    if not content or len(content) > _MAX_PYTHON_TOOL_BYTES:
        raise ManifestValidationError(
            f"Bundle Python tool must be 1-{_MAX_PYTHON_TOOL_BYTES} bytes: {relative_text}"
        )
    try:
        source = content.decode("utf-8")
        tree = ast.parse(source, filename=relative.as_posix())
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ManifestValidationError(
            f"invalid Bundle Python tool source: {relative_text}: {error}"
        ) from error
    metadata_value: object | None = None
    has_run = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run":
            has_run = True
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOL_SPEC"
            for target in node.targets
        ):
            try:
                metadata_value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError) as error:
                raise ManifestValidationError(
                    f"Bundle Python TOOL_SPEC must be a literal object: {relative_text}"
                ) from error
    if not has_run:
        raise ManifestValidationError(
            f"Bundle Python tool must define run(arguments): {relative_text}"
        )
    if not isinstance(metadata_value, dict):
        raise ManifestValidationError(
            f"Bundle Python tool must define literal TOOL_SPEC: {relative_text}"
        )
    metadata = cast(dict[str, object], metadata_value)
    name = metadata.get("name")
    description = metadata.get("description")
    input_schema = metadata.get("input_schema")
    if not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
        raise ManifestValidationError(f"invalid Bundle Python tool name: {relative_text}")
    if not isinstance(description, str) or not description.strip():
        raise ManifestValidationError(
            f"Bundle Python tool description is required: {relative_text}"
        )
    if not isinstance(input_schema, dict):
        raise ManifestValidationError(
            f"Bundle Python tool input_schema must be a JSON object schema: {relative_text}"
        )
    typed_input_schema = cast(dict[str, object], input_schema)
    if typed_input_schema.get("type") != "object":
        raise ManifestValidationError(
            f"Bundle Python tool input_schema must be a JSON object schema: {relative_text}"
        )
    return PythonToolSnapshot(
        reference=reference,
        path=relative.as_posix(),
        name=name,
        description=description.strip(),
        inputSchema=cast(dict[str, Any], typed_input_schema),
        contentBase64=base64.b64encode(content).decode("ascii"),
        sha256=hashlib.sha256(content).hexdigest(),
        sizeBytes=len(content),
    )


def _load_tool_directory(
    root: Path,
    manifest: AgentManifest,
) -> ToolDirectorySnapshot | None:
    path = root / TOOL_DIRECTORY_FILENAME
    if not path.exists():
        if manifest.spec.tool_exposure_mode == "on_demand":
            raise ManifestValidationError("on-demand tool exposure requires tool-directory.json")
        return None
    if path.is_symlink() or not path.is_file():
        raise ManifestValidationError("tool-directory.json must be a regular file")
    if path.stat().st_size > _MAX_TOOL_DIRECTORY_BYTES:
        raise ManifestValidationError(
            f"tool-directory.json exceeds {_MAX_TOOL_DIRECTORY_BYTES} bytes"
        )
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        directory = ToolDirectorySnapshot.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
        raise ManifestValidationError(f"invalid tool-directory.json: {error}") from error
    if directory.exposure_mode != manifest.spec.tool_exposure_mode:
        raise ManifestValidationError(
            "tool directory exposure mode does not match the Agent Manifest"
        )

    expected_builtins = {tool.builtin for tool in manifest.spec.tools if tool.builtin is not None}
    actual_builtins = {
        entry.logical_reference for entry in directory.entries if entry.source == "builtin"
    }
    expected_mcp = {tool.mcp for tool in manifest.spec.tools if tool.mcp is not None}
    actual_mcp = {entry.logical_reference for entry in directory.entries if entry.source == "mcp"}
    expected_python = {
        tool.python_entry for tool in manifest.spec.tools if tool.python_entry is not None
    }
    actual_python = {
        entry.logical_reference for entry in directory.entries if entry.source == "python"
    }
    if (
        expected_builtins != actual_builtins
        or expected_mcp != actual_mcp
        or expected_python != actual_python
    ):
        raise ManifestValidationError(
            "tool directory references do not exactly match the Agent Manifest"
        )
    return directory


def materialize_skill_snapshots(
    snapshot: AgentManifestSnapshot, workspace: str | Path
) -> tuple[str, ...]:
    """Materialize immutable Skills using the layout discovered by Claude Agent SDK."""

    return materialize_skill_snapshot_set((snapshot,), workspace)


def materialize_skill_snapshot_set(
    snapshots: Sequence[AgentManifestSnapshot], workspace: str | Path
) -> tuple[str, ...]:
    """Materialize main/subagent Skills once, rejecting conflicting names."""

    skills_root = Path(workspace).resolve() / ".claude" / "skills"
    if skills_root.exists():
        if skills_root.is_symlink():
            raise ManifestValidationError("workspace Skill root cannot be a symlink")
        shutil.rmtree(skills_root)
    skills_root.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    skills_by_name: dict[str, SkillSnapshot] = {}
    for snapshot in snapshots:
        for skill in snapshot.skill_snapshots:
            existing = skills_by_name.get(skill.name)
            if existing is not None and existing.content_hash != skill.content_hash:
                raise ManifestValidationError(f"conflicting immutable Skill name: {skill.name}")
            skills_by_name[skill.name] = skill
    for skill in sorted(skills_by_name.values(), key=lambda item: item.name):
        target_root = (skills_root / skill.name).resolve()
        if not target_root.is_relative_to(skills_root):
            raise ManifestValidationError(f"unsafe Skill name: {skill.name}")
        for file in skill.files:
            target = (target_root / file.path).resolve()
            if not target.is_relative_to(target_root):
                raise ManifestValidationError(f"unsafe Skill snapshot path: {file.path}")
            try:
                content = base64.b64decode(file.content_base64, validate=True)
            except ValueError as error:
                raise ManifestValidationError(
                    f"invalid base64 Skill snapshot: {skill.name}/{file.path}"
                ) from error
            if (
                len(content) != file.size_bytes
                or hashlib.sha256(content).hexdigest() != file.sha256
            ):
                raise ManifestValidationError(f"corrupt Skill snapshot: {skill.name}/{file.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        names.append(skill.name)
    return tuple(names)


def materialize_python_tool_snapshot_set(
    snapshots: Sequence[AgentManifestSnapshot], workspace: str | Path
) -> dict[str, dict[str, Path]]:
    """Materialize immutable Bundle Python operators before Sandbox prepare."""

    workspace_root = Path(workspace).resolve()
    root = workspace_root / ".harness-runtime" / "bundle-tools"
    if root.exists():
        if root.is_symlink():
            raise ManifestValidationError("workspace Bundle tool root cannot be a symlink")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    materialized: dict[str, dict[str, Path]] = {}
    for snapshot in snapshots:
        namespace = snapshot.content_hash[:16]
        namespace_root = (root / namespace).resolve()
        if not namespace_root.is_relative_to(root):
            raise ManifestValidationError("unsafe Bundle tool namespace")
        references: dict[str, Path] = {}
        for tool in snapshot.python_tool_snapshots:
            target = (namespace_root / tool.path).resolve()
            if not target.is_relative_to(namespace_root):
                raise ManifestValidationError(f"unsafe Bundle tool path: {tool.path}")
            try:
                content = base64.b64decode(tool.content_base64, validate=True)
            except ValueError as error:
                raise ManifestValidationError(
                    f"invalid Bundle tool base64: {tool.reference}"
                ) from error
            if (
                len(content) != tool.size_bytes
                or hashlib.sha256(content).hexdigest() != tool.sha256
            ):
                raise ManifestValidationError(f"corrupt Bundle tool: {tool.reference}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            references[tool.reference] = target.relative_to(workspace_root)
        materialized[snapshot.content_hash] = references
    return materialized


def load_manifest(
    path: str | Path,
    *,
    environment: Literal["local", "test", "production"] = "local",
) -> AgentManifestSnapshot:
    """Load, validate, resolve and hash one Agent Manifest."""

    manifest_path = Path(path).resolve()
    try:
        raw_value = yaml.safe_load(manifest_path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ManifestValidationError(f"cannot read manifest: {error}") from error
    if not isinstance(raw_value, dict):
        raise ManifestValidationError("manifest must be a YAML object")

    raw = cast(dict[str, Any], raw_value)
    _assert_no_inline_secrets(raw)
    try:
        manifest = AgentManifest.model_validate(raw)
    except ValidationError as error:
        raise ManifestValidationError(str(error)) from error

    if environment == "production":
        for subagent in manifest.spec.subagents:
            if subagent.ref.endswith("@latest") or "@" not in subagent.ref:
                raise ManifestValidationError(
                    "production subagent references require an explicit version, "
                    f"not latest: {subagent.ref}"
                )

    root = manifest_path.parent.resolve()
    prompt_path = _resolve_file(root, manifest.spec.prompt.system, "system prompt")
    system_prompt = prompt_path.read_text()
    tool_directory = _load_tool_directory(root, manifest)

    digest = hashlib.sha256()
    canonical = json.dumps(
        manifest.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    digest.update(canonical.encode())
    digest.update(prompt_path.relative_to(root).as_posix().encode())
    digest.update(system_prompt.encode())
    skill_snapshots = tuple(_snapshot_skill(root, skill) for skill in sorted(manifest.spec.skills))
    skill_names = [skill.name for skill in skill_snapshots]
    duplicate_names = sorted({name for name in skill_names if skill_names.count(name) > 1})
    if duplicate_names:
        raise ManifestValidationError(f"duplicate Skill name: {', '.join(duplicate_names)}")
    for skill in skill_snapshots:
        digest.update(skill.source.encode())
        digest.update(skill.content_hash.encode())
    python_tool_snapshots = tuple(
        _snapshot_python_tool(root, reference)
        for reference in sorted(
            tool.python_entry
            for tool in manifest.spec.tools
            if tool.python_entry is not None and tool.python_entry.startswith("bundle:")
        )
    )
    for tool in python_tool_snapshots:
        digest.update(tool.reference.encode())
        digest.update(tool.sha256.encode())
    if tool_directory is not None:
        digest.update(TOOL_DIRECTORY_FILENAME.encode())
        digest.update(tool_directory.content_hash.encode())

    return AgentManifestSnapshot(
        manifest=manifest,
        system_prompt=system_prompt,
        skill_snapshots=skill_snapshots,
        pythonToolSnapshots=python_tool_snapshots,
        tool_directory=tool_directory,
        content_hash=digest.hexdigest(),
    )
