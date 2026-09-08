"""Immutable models used by the Agent Studio authoring control plane."""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from harness.core.manifest import ToolExposureMode
from harness.core.models import AgentRuntimeType
from harness.evals.suite import EvalCase


class StudioModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class AgentTemplate(StrEnum):
    ANALYST = "analyst"
    OPERATOR = "operator"
    ORCHESTRATOR = "orchestrator"


class CapabilityRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NetworkAccess(StrEnum):
    NONE = "none"
    INTERNAL = "internal"
    EXTERNAL = "external"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationStage(StrEnum):
    PUBLISH = "publish"
    PRODUCTION = "production"


class DraftModelSelection(StudioModel):
    route_id: str = Field(alias="routeId", min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = Field(
        default=None,
        alias="reasoningEffort",
    )
    fallback_route_id: str | None = Field(default=None, alias="fallbackRouteId")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    required_capabilities: tuple[str, ...] = Field(
        default=("streaming", "tool_use"), alias="requiredCapabilities"
    )


class DraftSkillFile(StudioModel):
    path: str = Field(min_length=1, max_length=512)
    content: str | None = Field(default=None, max_length=64 * 1024 * 1024)
    content_base64: str | None = Field(
        default=None,
        alias="contentBase64",
        max_length=90 * 1024 * 1024,
    )
    retained: bool = False
    size_bytes: int | None = Field(default=None, alias="sizeBytes", ge=0)
    content_sha256: str | None = Field(
        default=None,
        alias="contentSha256",
        pattern=r"^[a-f0-9]{64}$",
    )
    binary: bool = False

    @model_validator(mode="after")
    def safe_relative_path(self) -> DraftSkillFile:
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or "\\" in self.path
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() == "SKILL.md"
        ):
            raise ValueError("Skill file path must be safe and cannot replace SKILL.md")
        content_count = int(self.content is not None) + int(self.content_base64 is not None)
        if self.retained:
            if content_count or self.size_bytes is None or self.content_sha256 is None:
                raise ValueError(
                    "Retained Skill file requires sizeBytes and contentSha256 without content"
                )
            return self
        if content_count != 1:
            raise ValueError("Skill file must contain exactly one of content or contentBase64")
        if self.content_base64 is not None:
            try:
                decoded = base64.b64decode(self.content_base64, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("Skill binary file contentBase64 is invalid") from error
            if len(decoded) > 64 * 1024 * 1024:
                raise ValueError("Skill file exceeds 64 MiB")
        return self


class DraftSkillSource(StudioModel):
    """Immutable provenance for a Skill copied from a managed package."""

    kind: Literal["platform"] = "platform"
    package_id: str = Field(alias="packageId", pattern=r"^[a-z][a-z0-9-]*$")
    package_revision: int = Field(alias="packageRevision", ge=1)
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=2_000)
    source_revision: str = Field(alias="sourceRevision", min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    modified: bool = False


class DraftSkill(StudioModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=512 * 1024)
    files: tuple[DraftSkillFile, ...] = ()
    file_count: int | None = Field(default=None, alias="fileCount", ge=0)
    files_truncated: bool = Field(default=False, alias="filesTruncated")
    source: DraftSkillSource | None = None

    @model_validator(mode="after")
    def unique_file_paths(self) -> DraftSkill:
        paths = [file.path for file in self.files]
        duplicates = sorted({path for path in paths if paths.count(path) > 1})
        if duplicates:
            raise ValueError(f"duplicate Skill file path: {', '.join(duplicates)}")
        return self


class PlatformSkillPackage(StudioModel):
    package_id: str = Field(alias="packageId", pattern=r"^[a-z][a-z0-9-]*$")
    revision: int = Field(ge=1)
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    tags: tuple[str, ...] = ()
    compatible_runtimes: tuple[AgentRuntimeType, ...] = Field(
        alias="compatibleRuntimes",
        min_length=1,
    )
    license: str = Field(min_length=1, max_length=100)
    source_url: str = Field(alias="sourceUrl", min_length=1, max_length=2_000)
    source_revision: str = Field(alias="sourceRevision", min_length=1, max_length=200)
    content_hash: str = Field(alias="contentHash", pattern=r"^[a-f0-9]{64}$")
    risk_level: Literal["low", "review"] = Field(alias="riskLevel")
    findings: tuple[str, ...] = ()
    skill: DraftSkill
    evaluation_cases: tuple[EvalCase, ...] = Field(alias="evaluationCases", min_length=1)


class PlatformSkillCatalog(StudioModel):
    revision: int = Field(ge=1)
    packages: tuple[PlatformSkillPackage, ...]


class InstallPlatformSkillRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    package_revision: int = Field(alias="packageRevision", ge=1)


class ImportedSkill(StudioModel):
    skill: DraftSkill
    source_content_hash: str = Field(alias="sourceContentHash", min_length=64, max_length=64)
    risk_level: Literal["low", "review"] = Field(alias="riskLevel")
    findings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class DraftPythonTool(StudioModel):
    """Editable Python operator packaged and executed inside the Run sandbox."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1, max_length=2_000)
    input_schema: dict[str, object] = Field(alias="inputSchema")
    code: str = Field(min_length=1, max_length=1024 * 1024)

    @model_validator(mode="after")
    def valid_operator_contract(self) -> DraftPythonTool:
        if self.input_schema.get("type") != "object":
            raise ValueError("Python tool inputSchema must be an object schema")
        if "def run(" not in self.code and "async def run(" not in self.code:
            raise ValueError("Python tool code must define run(arguments)")
        return self


class DraftWorkspace(StudioModel):
    restore_session: bool = Field(default=True, alias="restoreSession")
    archive_on_complete: bool = Field(default=True, alias="archiveOnComplete")


class DraftLimits(StudioModel):
    max_turns: int | None = Field(default=None, alias="maxTurns", ge=1)
    max_tool_calls: int | None = Field(
        default=256,
        alias="maxToolCalls",
        ge=1,
        le=4096,
    )
    timeout_seconds: int | None = Field(
        default=None,
        alias="timeoutSeconds",
        ge=1,
        le=86_400,
    )
    max_budget_usd: float | None = Field(default=None, alias="maxBudgetUsd", gt=0)
    max_model_tokens: int | None = Field(default=None, alias="maxModelTokens", ge=1)
    max_subagents: int = Field(default=8, alias="maxSubagents", ge=1, le=32)
    max_subagent_tasks: int = Field(default=16, alias="maxSubagentTasks", ge=1, le=128)
    max_concurrent_subagents: int = Field(default=4, alias="maxConcurrentSubagents", ge=1, le=16)
    max_subagent_usage_units: int | None = Field(default=None, alias="maxSubagentUsageUnits", gt=0)


    @field_validator(
        "max_budget_usd", "max_model_tokens", "max_subagent_usage_units", mode="before"
    )
    @classmethod
    def discard_operational_limits(cls, value: object) -> None:
        """New, imported and edited drafts never acquire monetary or Token quotas."""
        return None


class DraftSubagent(StudioModel):
    alias: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    ref: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*@[^@]+$")
    responsibility: str = Field(min_length=1, max_length=500)
    background: bool = False


class DraftTaskContract(StudioModel):
    """Business-facing contract compiled into Prompt and acceptance tests."""

    goal: str = Field(min_length=1, max_length=2_000)
    audience: str = Field(default="当前用户", min_length=1, max_length=500)
    inputs: tuple[str, ...] = Field(min_length=1, max_length=20)
    outputs: tuple[str, ...] = Field(min_length=1, max_length=20)
    constraints: tuple[str, ...] = Field(default=(), max_length=20)
    examples: tuple[str, ...] = Field(default=(), max_length=10)


class AgentDraftSpec(StudioModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    version: str = Field(default="0.1.0", min_length=1)
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    domain: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    template: AgentTemplate = AgentTemplate.ANALYST
    task_contract: DraftTaskContract | None = Field(default=None, alias="taskContract")
    runtime: AgentRuntimeType = "claude-agent-sdk"
    model: DraftModelSelection
    system_prompt: str = Field(alias="systemPrompt", min_length=1, max_length=512 * 1024)
    skills: tuple[DraftSkill, ...] = ()
    builtin_tools: tuple[str, ...] = Field(default=(), alias="builtinTools")
    python_tools: tuple[DraftPythonTool, ...] = Field(default=(), alias="pythonTools")
    mcp_servers: tuple[str, ...] = Field(default=(), alias="mcpServers")
    tool_exposure_mode: ToolExposureMode = Field(
        default="eager",
        alias="toolExposureMode",
    )
    knowledge_references: tuple[str, ...] = Field(
        default=(),
        alias="knowledgeReferences",
    )
    subagents: tuple[DraftSubagent, ...] = ()
    permission_policy: str = Field(alias="permissionPolicy", min_length=1)
    execution_profile: str = Field(
        default="isolated-default", alias="executionProfile", min_length=1
    )
    workspace: DraftWorkspace = DraftWorkspace()
    limits: DraftLimits = DraftLimits()
    evaluation_enabled: bool = Field(default=True, alias="evaluationEnabled")
    evaluation_cases: tuple[EvalCase, ...] = Field(min_length=1, alias="evaluationCases")

    @model_validator(mode="after")
    def unique_capabilities(self) -> AgentDraftSpec:
        for label, values in (
            ("builtin tool", self.builtin_tools),
            ("MCP server", self.mcp_servers),
            ("Knowledge Base", self.knowledge_references),
        ):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")
        aliases = [subagent.alias for subagent in self.subagents]
        duplicate_aliases = sorted({alias for alias in aliases if aliases.count(alias) > 1})
        if duplicate_aliases:
            raise ValueError(f"duplicate subagent alias: {', '.join(duplicate_aliases)}")
        skill_names = [skill.name for skill in self.skills]
        duplicate_skills = sorted({name for name in skill_names if skill_names.count(name) > 1})
        if duplicate_skills:
            raise ValueError(f"duplicate Skill: {', '.join(duplicate_skills)}")
        python_tool_names = [tool.name for tool in self.python_tools]
        duplicate_python_tools = sorted(
            {name for name in python_tool_names if python_tool_names.count(name) > 1}
        )
        if duplicate_python_tools:
            raise ValueError(f"duplicate Python tool: {', '.join(duplicate_python_tools)}")
        return self


class AgentDraft(StudioModel):
    # Authoring placement only: null is an independent entry, otherwise owned
    # by a parent draft. This does not mutate previously published snapshots.
    parent_draft_id: str | None = Field(default=None, alias="parentDraftId", min_length=1)
    draft_id: str = Field(alias="draftId", min_length=1)
    tenant_id: str = Field(alias="tenantId", min_length=1)
    revision: int = Field(ge=1)
    spec: AgentDraftSpec
    created_by: str = Field(alias="createdBy", min_length=1)
    updated_by: str = Field(alias="updatedBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    # Stable Agent identity this draft belongs to. None only for drafts
    # persisted before the 0023 migration backfill.
    agent_id: str | None = Field(default=None, alias="agentId")
    # Team space of a shared draft (None for personal drafts).
    space_id: str | None = Field(default=None, alias="spaceId")
    published_version: str | None = Field(default=None, alias="publishedVersion")
    published_hash: str | None = Field(
        default=None, alias="publishedHash", pattern=r"^[a-f0-9]{64}$"
    )
    published_package_hash: str | None = Field(
        default=None, alias="publishedPackageHash", pattern=r"^[a-f0-9]{64}$"
    )


class InstalledSkill(StudioModel):
    draft: AgentDraft
    skill_name: str = Field(alias="skillName")
    source_content_hash: str = Field(alias="sourceContentHash", min_length=64, max_length=64)
    risk_level: Literal["low", "review"] = Field(alias="riskLevel")
    findings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    file_count: int = Field(alias="fileCount", ge=0)
    binary_file_count: int = Field(alias="binaryFileCount", ge=0)


class CreateAgentDraftRequest(StudioModel):
    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    domain: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    template: AgentTemplate = AgentTemplate.ANALYST
    # Workspace shared draft binding: both must be provided together.
    agent_id: str | None = Field(default=None, alias="agentId", min_length=1)
    space_id: str | None = Field(default=None, alias="spaceId", min_length=1)

    @model_validator(mode="after")
    def shared_draft_binding(self) -> CreateAgentDraftRequest:
        if (self.agent_id is None) != (self.space_id is None):
            raise ValueError("agentId and spaceId must be provided together")
        return self


class ReplaceAgentDraftRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    spec: AgentDraftSpec


class CreateInternalSubagentRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    display_name: str = Field(alias="displayName", min_length=1, max_length=100)
    responsibility: str = Field(min_length=2, max_length=500)

    @model_validator(mode="after")
    def non_empty_text(self) -> CreateInternalSubagentRequest:
        if not self.display_name.strip() or len(self.responsibility.strip()) < 2:
            raise ValueError("请填写名称和职责")
        return self


class AgentDraftPlacementRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    parent_draft_id: str | None = Field(alias="parentDraftId", min_length=1)


class CreatedInternalSubagent(StudioModel):
    parent: AgentDraft
    child: AgentDraft


class PublishAgentDraftRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class PublishedAgentVersion(StudioModel):
    tenant_id: str
    name: str
    version: str
    status: Literal["published"]
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_id: str | None = None
    created_at: datetime


class ImportedAgentBundle(StudioModel):
    draft: AgentDraft
    source_content_hash: str = Field(
        alias="sourceContentHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    source_package_hash: str = Field(
        alias="sourcePackageHash",
        pattern=r"^[a-f0-9]{64}$",
    )
    lossless: bool
    round_trip_verified: bool = Field(alias="roundTripVerified")
    warnings: tuple[str, ...] = ()


class AgentDraftSummary(StudioModel):
    parent_draft_id: str | None = Field(default=None, alias="parentDraftId")
    draft_id: str = Field(alias="draftId")
    agent_id: str | None = Field(default=None, alias="agentId")
    space_id: str | None = Field(default=None, alias="spaceId")
    name: str
    display_name: str = Field(alias="displayName")
    domain: str
    version: str
    template: AgentTemplate
    goal: str
    primary_output: str = Field(alias="primaryOutput")
    primary_constraint: str | None = Field(default=None, alias="primaryConstraint")
    skill_count: int = Field(alias="skillCount", ge=0)
    tool_count: int = Field(alias="toolCount", ge=0)
    network_tools_enabled: bool = Field(alias="networkToolsEnabled")
    revision: int
    updated_at: datetime = Field(alias="updatedAt")
    published_version: str | None = Field(default=None, alias="publishedVersion")

    @classmethod
    def from_draft(cls, draft: AgentDraft) -> AgentDraftSummary:
        task_contract = draft.spec.task_contract
        return cls(
            parentDraftId=draft.parent_draft_id,
            draftId=draft.draft_id,
            agentId=draft.agent_id,
            spaceId=draft.space_id,
            name=draft.spec.name,
            displayName=draft.spec.display_name,
            domain=draft.spec.domain,
            version=draft.spec.version,
            template=draft.spec.template,
            goal=task_contract.goal if task_contract else draft.spec.description,
            primaryOutput=(
                task_contract.outputs[0]
                if task_contract and task_contract.outputs
                else "按 System Prompt 生成可核验结果"
            ),
            primaryConstraint=(
                task_contract.constraints[0]
                if task_contract and task_contract.constraints
                else None
            ),
            skillCount=len(draft.spec.skills),
            toolCount=(
                len(draft.spec.builtin_tools)
                + len(draft.spec.python_tools)
                + len(draft.spec.mcp_servers)
            ),
            networkToolsEnabled=bool(
                {"WebSearch", "WebFetch"}.intersection(draft.spec.builtin_tools)
            ),
            revision=draft.revision,
            updatedAt=draft.updated_at,
            publishedVersion=draft.published_version,
        )


class ModelRouteCapability(StudioModel):
    route_id: str = Field(alias="routeId", pattern=r"^[a-z][a-z0-9-]*$")
    label: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=80)
    models: tuple[str, ...] = Field(min_length=1)
    capabilities: tuple[str, ...]
    model_type: Literal["chat", "vision", "image_generation", "video_generation"] = Field(
        default="chat", alias="modelType"
    )
    base_url: str | None = Field(default=None, alias="baseUrl", max_length=2048)
    api_format: Literal[
        "anthropic_compatible", "openai_compatible", "openai_images", "openai_videos"
    ] = Field(default="anthropic_compatible", alias="apiFormat")
    auth_scheme: Literal["bearer", "x-api-key", "none"] = Field(
        default="bearer", alias="authScheme"
    )
    credential_managed: bool = Field(default=True, alias="credentialManaged")
    credential_reference: str | None = Field(
        default=None,
        alias="credentialReference",
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    version: int = Field(default=1, ge=1)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_model_connection(self) -> ModelRouteCapability:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("duplicate model capability")
        required = {
            "chat": {"streaming", "tool_use"},
            "vision": {"streaming", "tool_use", "vision"},
            "image_generation": {"image_generation"},
            "video_generation": {"video_generation"},
        }[self.model_type]
        if not required.issubset(self.capabilities):
            raise ValueError(
                f"{self.model_type} model is missing required capabilities: "
                + ", ".join(sorted(required - set(self.capabilities)))
            )
        if self.model_type == "image_generation" and self.api_format != "openai_images":
            raise ValueError("image generation models must use the openai_images API format")
        if self.model_type != "image_generation" and self.api_format == "openai_images":
            raise ValueError("openai_images is only valid for image generation models")
        if self.model_type == "video_generation" and self.api_format != "openai_videos":
            raise ValueError("video generation models must use the openai_videos API format")
        if self.model_type != "video_generation" and self.api_format == "openai_videos":
            raise ValueError("openai_videos is only valid for video generation models")
        if self.auth_scheme == "none" and self.model_type != "video_generation":
            raise ValueError(
                "unauthenticated model connections are only valid for video generation"
            )
        if self.base_url is not None:
            parsed = urlsplit(self.base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "model Base URL must be HTTP(S) without credentials, query, or fragment"
                )
        return self


class BuiltinToolCapability(StudioModel):
    name: str
    label: str
    description: str
    risk: CapabilityRisk
    execution_location: str = Field(alias="executionLocation")
    approval_behavior: str = Field(alias="approvalBehavior")


_MCP_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"
_MCP_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SENSITIVE_MCP_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "api-key",
        "apikey",
    }
)


def _validate_public_mcp_headers(headers: dict[str, str]) -> None:
    if len(headers) > 20:
        raise ValueError("MCP custom headers cannot exceed 20 entries")
    normalized: set[str] = set()
    for name, value in headers.items():
        lowered = name.lower()
        if not name or len(name) > 128 or not _MCP_HEADER_NAME_PATTERN.fullmatch(name):
            raise ValueError("MCP custom header name is invalid")
        if lowered in normalized:
            raise ValueError("duplicate MCP custom header")
        if lowered in _SENSITIVE_MCP_HEADERS:
            raise ValueError("MCP secrets must use managed authentication, not custom headers")
        if len(value) > 1024 or "\n" in value or "\r" in value:
            raise ValueError("MCP custom header value is invalid")
        normalized.add(lowered)


class McpCapability(StudioModel):
    reference: str = Field(pattern=_MCP_IDENTIFIER_PATTERN)
    owner_user_id: str | None = Field(default=None, alias="ownerUserId", min_length=1)
    allowed_execution_profile_ids: tuple[str, ...] = Field(
        default=(),
        alias="allowedExecutionProfileIds",
    )
    category: Literal["tool", "knowledge"] = "tool"
    server_name: str | None = Field(
        default=None,
        alias="serverName",
        pattern=_MCP_IDENTIFIER_PATTERN,
    )
    label: str
    description: str
    endpoint_url: str | None = Field(default=None, alias="endpointUrl", max_length=2048)
    transport: Literal["http", "sse"] = "http"
    custom_headers: dict[str, str] = Field(default_factory=dict, alias="customHeaders")
    tools: tuple[str, ...]
    risk: CapabilityRisk
    network_access: NetworkAccess = Field(alias="networkAccess")
    sends_user_data: bool = Field(alias="sendsUserData")
    read_only: bool = Field(default=False, alias="readOnly")
    credential_managed: bool = Field(default=True, alias="credentialManaged")
    execution_location: str = Field(alias="executionLocation")
    preflight_required: bool = Field(default=True, alias="preflightRequired")
    credential_reference: str | None = Field(
        default=None,
        alias="credentialReference",
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    auth_mode: Literal["none", "bearer", "header", "query"] = Field(
        default="none",
        alias="authMode",
    )
    auth_name: str | None = Field(default=None, alias="authName", max_length=128)
    auth_key: str = Field(
        default="authorization",
        alias="authKey",
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    version: int = Field(default=1, ge=1)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_endpoint_and_auth(self) -> McpCapability:
        _validate_public_mcp_headers(self.custom_headers)
        if self.endpoint_url is not None:
            parsed = urlsplit(self.endpoint_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "MCP endpoint must be HTTP(S) without credentials, query, or fragment"
                )
        if self.auth_mode in {"header", "query"} and not self.auth_name:
            raise ValueError("MCP header/query authentication requires authName")
        if (
            self.auth_mode == "header"
            and self.auth_name
            and self.auth_name.lower() in {name.lower() for name in self.custom_headers}
        ):
            raise ValueError("MCP authentication header duplicates a custom header")
        if self.auth_mode != "none" and self.credential_reference is None:
            raise ValueError("authenticated MCP requires credentialReference")
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("duplicate MCP tool")
        if len(self.allowed_execution_profile_ids) != len(set(self.allowed_execution_profile_ids)):
            raise ValueError("duplicate MCP Execution Profile")
        return self


class McpDiscoveryRequest(StudioModel):
    reference: str = Field(pattern=_MCP_IDENTIFIER_PATTERN)
    server_name: str = Field(alias="serverName", pattern=_MCP_IDENTIFIER_PATTERN)
    endpoint_url: str = Field(alias="endpointUrl", min_length=1, max_length=2048)
    network_access: Literal["internal", "external"] = Field(alias="networkAccess")
    custom_headers: dict[str, str] = Field(default_factory=dict, alias="customHeaders")
    auth_mode: Literal["none", "bearer", "header", "query"] = Field(
        default="none",
        alias="authMode",
    )
    auth_name: str | None = Field(default=None, alias="authName", max_length=128)
    auth_key: str = Field(
        default="authorization",
        alias="authKey",
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    credential_value: SecretStr | None = Field(
        default=None,
        alias="credentialValue",
        min_length=1,
        max_length=16_384,
    )

    @model_validator(mode="after")
    def valid_custom_headers(self) -> McpDiscoveryRequest:
        _validate_public_mcp_headers(self.custom_headers)
        if (
            self.auth_mode == "header"
            and self.auth_name
            and self.auth_name.lower() in {name.lower() for name in self.custom_headers}
        ):
            raise ValueError("MCP authentication header duplicates a custom header")
        return self


class McpDiscoveredTool(StudioModel):
    name: str
    canonical_name: str = Field(alias="canonicalName")
    title: str | None = None
    description: str = ""


class McpDiscoveryResult(StudioModel):
    endpoint_url: str = Field(alias="endpointUrl")
    transport: Literal["http", "sse"]
    server_name: str = Field(alias="serverName")
    server_title: str | None = Field(default=None, alias="serverTitle")
    server_version: str | None = Field(default=None, alias="serverVersion")
    latency_ms: int = Field(alias="latencyMs", ge=0)
    tools: tuple[McpDiscoveredTool, ...]


class PolicyCapability(StudioModel):
    policy_id: str = Field(alias="policyId")
    label: str
    description: str
    risk: CapabilityRisk
    version: int = Field(default=1, ge=1)
    enabled: bool = True


class ExecutionProfileMetadata(StudioModel):
    profile_id: str = Field(alias="profileId", pattern=r"^[a-z][a-z0-9-]*$")
    label: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    sandbox_provider: Literal["local", "daytona", "e2b", "gvisor"] = Field(alias="sandboxProvider")
    network_access: tuple[NetworkAccess, ...] = Field(alias="networkAccess")
    risk: CapabilityRisk
    cpu_millis: int = Field(default=1000, alias="cpuMillis", ge=100, le=16_000)
    memory_mib: int = Field(default=2048, alias="memoryMiB", ge=128, le=65_536)
    disk_mib: int = Field(default=10_240, alias="diskMiB", ge=512, le=1_048_576)
    ttl_seconds: int = Field(default=3600, alias="ttlSeconds", ge=60, le=86_400)
    network_policy_id: str = Field(
        default="deny-by-default",
        alias="networkPolicyId",
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    allowed_mcp_references: tuple[str, ...] = Field(default=(), alias="allowedMcpReferences")
    provider_config_reference: str = Field(
        default="platform-default",
        alias="providerConfigReference",
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    production_allowed: bool = Field(default=True, alias="productionAllowed")
    version: int = Field(default=1, ge=1)
    enabled: bool = True


class TemplateCapability(StudioModel):
    template: AgentTemplate
    label: str
    description: str


class RuntimeCapability(StudioModel):
    """Version-aware feature declaration consumed by Compiler and Builder."""

    runtime: AgentRuntimeType
    label: str
    stability: Literal["stable", "preview", "experimental"] = "stable"
    capabilities: tuple[str, ...]
    model_api_formats: tuple[
        Literal[
            "anthropic_compatible",
            "openai_compatible",
            "openai_images",
            "openai_videos",
        ],
        ...,
    ] = Field(alias="modelApiFormats")
    limitations: tuple[str, ...] = ()


class CapabilityCatalog(StudioModel):
    model_routes: tuple[ModelRouteCapability, ...] = Field(alias="modelRoutes")
    builtin_tools: tuple[BuiltinToolCapability, ...] = Field(alias="builtinTools")
    mcp_servers: tuple[McpCapability, ...] = Field(alias="mcpServers")
    policies: tuple[PolicyCapability, ...]
    execution_profiles: tuple[ExecutionProfileMetadata, ...] = Field(
        default=(), alias="executionProfiles"
    )
    templates: tuple[TemplateCapability, ...]
    runtime_capabilities: tuple[RuntimeCapability, ...] = Field(
        default=(), alias="runtimeCapabilities"
    )
    agent_model_bindings: dict[str, str] = Field(default_factory=dict, alias="agentModelBindings")

    @model_validator(mode="after")
    def unique_managed_ids(self) -> CapabilityCatalog:
        collections = (
            ("model route", [item.route_id for item in self.model_routes]),
            ("policy", [item.policy_id for item in self.policies]),
            (
                "execution profile",
                [item.profile_id for item in self.execution_profiles],
            ),
        )
        for label, identifiers in collections:
            duplicates = sorted(
                {identifier for identifier in identifiers if identifiers.count(identifier) > 1}
            )
            if duplicates:
                raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")
        mcp_keys = [(item.owner_user_id, item.reference) for item in self.mcp_servers]
        duplicate_mcp = sorted(
            {
                f"{owner or 'platform'}:{reference}"
                for owner, reference in mcp_keys
                if mcp_keys.count((owner, reference)) > 1
            }
        )
        if duplicate_mcp:
            raise ValueError(f"duplicate MCP: {', '.join(duplicate_mcp)}")
        route_ids = {item.route_id for item in self.model_routes}
        invalid_agents = sorted(
            name
            for name, route_id in self.agent_model_bindings.items()
            if not name or not re.fullmatch(r"[a-z][a-z0-9-]*", name) or route_id not in route_ids
        )
        if invalid_agents:
            raise ValueError("invalid Agent model bindings: " + ", ".join(invalid_agents))
        return self


class CapabilityCatalogRecord(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    revision: int = Field(ge=1)
    catalog: CapabilityCatalog
    updated_by: str = Field(alias="updatedBy", min_length=1)
    updated_at: datetime = Field(alias="updatedAt")


class ReplaceCapabilityCatalogRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    catalog: CapabilityCatalog


class CatalogImpact(StudioModel):
    resource_type: Literal["modelRoute", "mcp", "policy", "executionProfile"] = Field(
        alias="resourceType"
    )
    resource_id: str = Field(alias="resourceId")
    draft_ids: tuple[str, ...] = Field(alias="draftIds")


class CatalogMutationResult(StudioModel):
    record: CapabilityCatalogRecord
    impact: CatalogImpact


CatalogManagedResource = (
    ModelRouteCapability | McpCapability | PolicyCapability | ExecutionProfileMetadata
)


class UpsertCatalogResourceRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    resource: CatalogManagedResource
    allowed_execution_profile_ids: tuple[str, ...] | None = Field(
        default=None,
        alias="allowedExecutionProfileIds",
    )

    @model_validator(mode="after")
    def unique_allowed_execution_profiles(self) -> UpsertCatalogResourceRequest:
        profile_ids = self.allowed_execution_profile_ids
        if profile_ids is not None and len(profile_ids) != len(set(profile_ids)):
            raise ValueError("allowed Execution Profile IDs must be unique")
        return self


class ValidationIssue(StudioModel):
    code: str
    message: str
    severity: ValidationSeverity
    path: str | None = None
    stage: ValidationStage = ValidationStage.PUBLISH
    related_references: tuple[str, ...] = Field(default=(), alias="relatedReferences")
    suggested_profile_ids: tuple[str, ...] = Field(default=(), alias="suggestedProfileIds")


class EffectiveAgentContract(StudioModel):
    model: str
    model_route: str = Field(alias="modelRoute")
    skills: int = Field(ge=0)
    builtin_tools: tuple[str, ...] = Field(alias="builtinTools")
    mcp_servers: tuple[str, ...] = Field(alias="mcpServers")
    tool_exposure_mode: ToolExposureMode = Field(alias="toolExposureMode")
    tool_directory_entries: int = Field(alias="toolDirectoryEntries", ge=0)
    knowledge_references: tuple[str, ...] = Field(alias="knowledgeReferences")
    network_access: NetworkAccess = Field(alias="networkAccess")
    network_summary: str = Field(alias="networkSummary")
    permission_policy: str = Field(alias="permissionPolicy")
    approval_summary: str = Field(alias="approvalSummary")
    sandbox: Literal["isolated"] = "isolated"
    risk: CapabilityRisk


class RuntimeCompatibility(StudioModel):
    runtime: AgentRuntimeType
    label: str
    stability: Literal["stable", "preview", "experimental"]
    compatible: bool
    capabilities: tuple[str, ...]
    limitations: tuple[str, ...]


class DraftValidationResult(StudioModel):
    ready: bool
    production_eligible: bool = Field(alias="productionEligible")
    issues: tuple[ValidationIssue, ...]
    contract: EffectiveAgentContract
    runtime_compatibility: RuntimeCompatibility = Field(alias="runtimeCompatibility")
    manifest_yaml: str = Field(alias="manifestYaml")
    content_hash: str | None = Field(default=None, alias="contentHash")
    package_hash: str | None = Field(default=None, alias="packageHash")
