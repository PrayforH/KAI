"""Compile Studio drafts into the existing reproducible Agent bundle format."""

from __future__ import annotations

import base64
import json
import pprint
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from harness.agent_package import (
    AgentPackageCheckError,
    AgentPackageReport,
    check_agent_package,
    pack_agent_package,
)
from harness.core.manifest import (
    TOOL_DIRECTORY_FILENAME,
    AgentManifest,
    ToolDirectoryEntry,
    ToolDirectorySnapshot,
)
from harness.evals.suite import EvalSuite
from harness.studio.bundle_format import (
    STUDIO_BUNDLE_METADATA_FILENAME,
    StudioBundleMetadata,
)
from harness.studio.models import (
    AgentDraft,
    CapabilityCatalog,
    CapabilityRisk,
    DraftSkill,
    DraftSkillSource,
    DraftValidationResult,
    EffectiveAgentContract,
    NetworkAccess,
    RuntimeCompatibility,
    ValidationIssue,
    ValidationSeverity,
    ValidationStage,
)
from harness.studio.platform_skills import default_platform_skill_catalog


class DraftCompilationError(ValueError):
    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        self.issues = issues
        super().__init__("Agent draft is not ready: " + "; ".join(i.message for i in issues))


@dataclass(frozen=True)
class CompiledAgentDraft:
    bundle: bytes
    filename: str
    report: AgentPackageReport
    manifest_yaml: str


_FUTURE_IMPORT = re.compile(r"(?m)^from __future__\s+import\s+[^\n]+\n?")


def _python_tool_source(code: str, metadata: str) -> str:
    """Insert generated metadata without invalidating module future imports."""
    generated = (
        "# Generated metadata is part of the editable Bundle tool contract.\n"
        f"TOOL_SPEC = {metadata}\n\n"
    )
    matches = list(_FUTURE_IMPORT.finditer(code))
    if not matches:
        return f"{generated}{code.strip()}\n"
    insertion = matches[-1].end()
    return f"{code[:insertion]}\n{generated}{code[insertion:].strip()}\n"


class AgentDraftCompiler:
    def __init__(
        self,
        catalog: CapabilityCatalog,
        *,
        catalog_revision: int = 1,
    ) -> None:
        self._catalog = catalog
        self._catalog_revision = catalog_revision

    def resolve_skills(self, draft: AgentDraft) -> tuple[DraftSkill, ...]:
        """Merge installed snapshots with platform catalog Skill references.

        Catalog references resolve against the immutable platform package
        catalog at compile time; each resolved copy pins the package revision
        and content hash via its DraftSkillSource so published versions stay
        reproducible even after the platform catalog moves forward.
        """

        spec = draft.spec
        if not spec.skill_references:
            return spec.skills
        resolved = list(spec.skills)
        names = {skill.name for skill in spec.skills}
        capabilities = {item.package_id: item for item in self._catalog.skills}
        packages = {
            package.package_id: package
            for package in default_platform_skill_catalog().packages
        }
        for reference in spec.skill_references:
            capability = capabilities.get(reference)
            package = packages.get(reference)
            if capability is None or package is None or not capability.enabled:
                continue
            if spec.runtime not in package.compatible_runtimes:
                continue
            if package.skill.name in names:
                continue
            source = DraftSkillSource(
                packageId=package.package_id,
                packageRevision=package.revision,
                sourceUrl=package.source_url,
                sourceRevision=package.source_revision,
                license=package.license,
                contentHash=package.content_hash,
                modified=False,
            )
            resolved.append(package.skill.model_copy(update={"source": source}))
            names.add(package.skill.name)
        return tuple(resolved)

    def render_manifest(self, draft: AgentDraft) -> str:
        spec = draft.spec
        required_capabilities = list(spec.model.required_capabilities)
        if spec.tool_exposure_mode == "on_demand" and "tool_search" not in required_capabilities:
            required_capabilities.append("tool_search")
        tools: list[dict[str, str]] = [{"builtin": name} for name in spec.builtin_tools]
        tools.extend({"python": f"bundle:tools/{tool.name}.py"} for tool in spec.python_tools)
        tools.extend({"mcp": reference} for reference in spec.mcp_servers)
        labels = {
            "domain": spec.domain,
            "template": spec.template.value,
            "display-name": spec.display_name,
            "description": spec.description,
            "evaluation-enabled": str(spec.evaluation_enabled).lower(),
        }
        if spec.model.reasoning_effort is not None:
            labels["codex-reasoning-effort"] = spec.model.reasoning_effort
        manifest = AgentManifest.model_validate(
            {
                "apiVersion": "harness/v1alpha1",
                "kind": "Agent",
                "metadata": {
                    "name": spec.name,
                    "version": spec.version,
                    "labels": labels,
                },
                "spec": {
                    "runtime": spec.runtime,
                    "model": {
                        "route": spec.model.route_id,
                        "model": spec.model.model,
                        "fallbackRoute": spec.model.fallback_route_id,
                        "fallbackModel": spec.model.fallback_model,
                        "requiredCapabilities": required_capabilities,
                    },
                    "prompt": {"system": "prompts/system.md"},
                    "skills": [
                        f"skills/{skill.name}" for skill in self.resolve_skills(draft)
                    ],
                    "tools": tools,
                    "toolExposureMode": spec.tool_exposure_mode,
                    "knowledgeReferences": list(spec.knowledge_references),
                    "subagents": [
                        {
                            "ref": subagent.ref,
                            "alias": subagent.alias,
                            "description": subagent.responsibility,
                            "background": subagent.background,
                        }
                        for subagent in spec.subagents
                    ],
                    "hooks": [],
                    "permissions": {"policy": spec.permission_policy},
                    "workspace": {
                        "mode": "isolated",
                        "restoreSession": spec.workspace.restore_session,
                        "archiveOnComplete": spec.workspace.archive_on_complete,
                    },
                    "limits": {
                        "maxTurns": spec.limits.max_turns,
                        "maxToolCalls": spec.limits.max_tool_calls,
                        "timeoutSeconds": spec.limits.timeout_seconds,
                        "maxBudgetUsd": None,
                        "maxModelTokens": None,
                        "maxSubagents": spec.limits.max_subagents,
                        "maxSubagentTasks": spec.limits.max_subagent_tasks,
                        "maxConcurrentSubagents": (spec.limits.max_concurrent_subagents),
                        "maxSubagentDepth": 1,
                        "maxSubagentUsageUnits": None,
                    },
                },
            }
        )
        return yaml.safe_dump(
            manifest.model_dump(mode="json", by_alias=True, exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        )

    def validate(self, draft: AgentDraft) -> DraftValidationResult:
        manifest_yaml = self.render_manifest(draft)
        issues = list(self._catalog_issues(draft))
        report: AgentPackageReport | None = None
        if not any(
            issue.severity is ValidationSeverity.ERROR and issue.stage is ValidationStage.PUBLISH
            for issue in issues
        ):
            with TemporaryDirectory(prefix="harness-agent-studio-check-") as directory:
                manifest = self._materialize(draft, Path(directory), manifest_yaml)
                try:
                    report = check_agent_package(manifest, environment="production")
                except AgentPackageCheckError as error:
                    for message in error.issues:
                        prefix = "evaluation suite is missing "
                        suffix = " coverage"
                        if message.startswith(prefix) and message.endswith(suffix):
                            tag = message[len(prefix) : -len(suffix)]
                            issues.append(
                                ValidationIssue(
                                    code=f"evaluation_coverage_{tag}_missing",
                                    message=message,
                                    severity=ValidationSeverity.ERROR,
                                    path="evaluationCases",
                                )
                            )
                        else:
                            issues.append(
                                ValidationIssue(
                                    code="package_check_failed",
                                    message=message,
                                    severity=ValidationSeverity.ERROR,
                                )
                            )
        issues.extend(self._deployment_warnings(draft))
        publish_ready = not any(
            issue.severity is ValidationSeverity.ERROR and issue.stage is ValidationStage.PUBLISH
            for issue in issues
        )
        runtime = next(
            (
                item
                for item in self._catalog.runtime_capabilities
                if item.runtime == draft.spec.runtime
            ),
            None,
        )
        runtime_issue_codes = {
            "runtime_unknown",
            "runtime_model_protocol_incompatible",
            "runtime_python_tools_unsupported",
            "runtime_knowledge_unsupported",
            "runtime_tool_search_unsupported",
            "runtime_subagents_unsupported",
            "runtime_mcp_transport_unsupported",
            "codex_responses_route_required",
            "codex_python_tools_unsupported",
            "codex_knowledge_unsupported",
            "codex_tool_search_unsupported",
        }
        return DraftValidationResult(
            ready=publish_ready,
            productionEligible=publish_ready
            and not any(
                issue.severity is ValidationSeverity.ERROR
                and issue.stage is ValidationStage.PRODUCTION
                for issue in issues
            ),
            issues=tuple(issues),
            contract=self.effective_contract(draft),
            runtimeCompatibility=RuntimeCompatibility(
                runtime=draft.spec.runtime,
                label=runtime.label if runtime is not None else draft.spec.runtime,
                stability=runtime.stability if runtime is not None else "experimental",
                compatible=not any(
                    issue.severity is ValidationSeverity.ERROR and issue.code in runtime_issue_codes
                    for issue in issues
                ),
                capabilities=runtime.capabilities if runtime is not None else (),
                limitations=runtime.limitations if runtime is not None else (),
            ),
            manifestYaml=manifest_yaml,
            contentHash=(report.snapshot.content_hash if report is not None else None),
            packageHash=(report.package_hash if report is not None else None),
        )

    def compile(self, draft: AgentDraft) -> CompiledAgentDraft:
        validation = self.validate(draft)
        if not validation.ready:
            raise DraftCompilationError(
                tuple(
                    issue
                    for issue in validation.issues
                    if issue.severity is ValidationSeverity.ERROR
                )
            )
        with TemporaryDirectory(prefix="harness-agent-studio-pack-") as directory:
            root = Path(directory)
            manifest = self._materialize(draft, root, validation.manifest_yaml)
            archive, report = pack_agent_package(manifest, output_directory=root / "dist")
            return CompiledAgentDraft(
                bundle=archive.read_bytes(),
                filename=archive.name,
                report=report,
                manifest_yaml=validation.manifest_yaml,
            )

    def effective_contract(self, draft: AgentDraft) -> EffectiveAgentContract:
        spec = draft.spec
        mcp_by_reference = {item.reference: item for item in self._catalog.mcp_servers}
        selected_mcp = [
            item
            for reference in spec.mcp_servers
            if (item := mcp_by_reference.get(reference)) is not None
        ]
        if {"WebSearch", "WebFetch"}.intersection(spec.builtin_tools):
            network = NetworkAccess.EXTERNAL
            network_summary = "平台内置公开网页检索；已选 MCP 继续按各自权限运行"
        elif any(item.network_access is NetworkAccess.EXTERNAL for item in selected_mcp):
            network = NetworkAccess.EXTERNAL
            network_summary = "仅通过审核过的外部 MCP 受控联网"
        elif any(item.network_access is NetworkAccess.INTERNAL for item in selected_mcp):
            network = NetworkAccess.INTERNAL
            network_summary = "仅访问注册的内部 MCP 服务"
        else:
            network = NetworkAccess.NONE
            network_summary = "未启用外部网络能力"

        if "Bash" in spec.builtin_tools or spec.python_tools:
            risk = CapabilityRisk.HIGH
            if spec.python_tools:
                approval = (
                    "自定义算子在隔离 Sandbox 执行；高风险、越界或不确定动作由策略拒绝或请求确认"
                )
            else:
                approval = (
                    "隔离 Sandbox 内常规 Bash 自动允许；"
                    "高风险、越界或不确定动作由策略拒绝或请求确认"
                )
        elif any(tool in spec.builtin_tools for tool in ("Write", "Edit", "Task")):
            risk = CapabilityRisk.MEDIUM
            approval = "工作区文件写入自动允许；委派受权限上限约束"
        elif network is not NetworkAccess.NONE:
            risk = CapabilityRisk.MEDIUM
            approval = "只读 MCP 自动允许，未声明能力隐式拒绝"
        else:
            risk = CapabilityRisk.LOW
            approval = "只读能力自动允许，未声明能力隐式拒绝"

        return EffectiveAgentContract(
            model=spec.model.model,
            modelRoute=spec.model.route_id,
            skills=len(self.resolve_skills(draft)),
            builtinTools=spec.builtin_tools,
            mcpServers=spec.mcp_servers,
            toolExposureMode=spec.tool_exposure_mode,
            toolDirectoryEntries=len(self.tool_directory(draft).entries),
            knowledgeReferences=spec.knowledge_references,
            networkAccess=network,
            networkSummary=network_summary,
            permissionPolicy=spec.permission_policy,
            approvalSummary=approval,
            sandbox="isolated",
            risk=risk,
        )

    def _catalog_issues(self, draft: AgentDraft) -> tuple[ValidationIssue, ...]:
        spec = draft.spec
        issues: list[ValidationIssue] = []
        runtimes = {
            capability.runtime: capability for capability in self._catalog.runtime_capabilities
        }
        runtime = runtimes.get(spec.runtime)
        runtime_features: set[str] = set(runtime.capabilities) if runtime is not None else set()
        if runtime is None:
            issues.append(
                ValidationIssue(
                    code="runtime_unknown",
                    message=f"Agent Runtime 未注册：{spec.runtime}",
                    severity=ValidationSeverity.ERROR,
                    path="runtime",
                )
            )
        routes = {route.route_id: route for route in self._catalog.model_routes}
        route = routes.get(spec.model.route_id)
        if route is None:
            issues.append(
                ValidationIssue(
                    code="model_route_unknown",
                    message=f"模型路由未注册：{spec.model.route_id}",
                    severity=ValidationSeverity.ERROR,
                    path="model.routeId",
                )
            )
        else:
            if route.model_type in {"image_generation", "video_generation"}:
                generation_kind = "图像" if route.model_type == "image_generation" else "视频"
                issues.append(
                    ValidationIssue(
                        code="model_route_not_conversational",
                        message=(
                            f"{generation_kind}生成模型不能作为 Agent 对话路由："
                            f"{spec.model.route_id}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="model.routeId",
                    )
                )
            if not route.enabled:
                issues.append(
                    ValidationIssue(
                        code="model_route_disabled",
                        message=f"模型路由已禁用：{spec.model.route_id}",
                        severity=ValidationSeverity.ERROR,
                        path="model.routeId",
                    )
                )
            if route.models and spec.model.model not in route.models:
                issues.append(
                    ValidationIssue(
                        code="model_not_available",
                        message=(f"模型 {spec.model.model} 不属于路由 {spec.model.route_id}"),
                        severity=ValidationSeverity.ERROR,
                        path="model.model",
                    )
                )
            if runtime is not None and route.api_format not in runtime.model_api_formats:
                issues.append(
                    ValidationIssue(
                        code=(
                            "codex_responses_route_required"
                            if spec.runtime == "codex-app-server"
                            else "runtime_model_protocol_incompatible"
                        ),
                        message=(
                            f"{runtime.label} 不支持模型路由协议 {route.api_format}；"
                            f"请选择 {', '.join(runtime.model_api_formats)} 路由"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="model.routeId",
                    )
                )
            missing = set(spec.model.required_capabilities) - set(route.capabilities)
            if missing:
                issues.append(
                    ValidationIssue(
                        code="model_capability_missing",
                        message=f"模型路由缺少能力：{', '.join(sorted(missing))}",
                        severity=ValidationSeverity.ERROR,
                        path="model.requiredCapabilities",
                    )
                )
            if spec.tool_exposure_mode == "on_demand" and "tool_search" not in route.capabilities:
                issues.append(
                    ValidationIssue(
                        code="tool_search_capability_missing",
                        message=(f"模型路由不支持按需工具加载：{spec.model.route_id}"),
                        severity=ValidationSeverity.ERROR,
                        path="toolExposureMode",
                    )
                )

        if {"WebSearch", "WebFetch"}.intersection(
            spec.builtin_tools
        ) and spec.runtime != "claude-agent-sdk":
            issues.append(
                ValidationIssue(
                    code="web_tools_runtime_unsupported",
                    message="平台内置联网目前支持 Claude SDK 运行时；其他运行时可保留 MCP。",
                    severity=ValidationSeverity.ERROR,
                    path="builtinTools",
                )
            )
        builtins = {tool.name for tool in self._catalog.builtin_tools}
        for name in spec.builtin_tools:
            if name not in builtins:
                issues.append(
                    ValidationIssue(
                        code="builtin_tool_unknown",
                        message=f"内建工具未注册：{name}",
                        severity=ValidationSeverity.ERROR,
                        path="builtinTools",
                    )
                )
        if runtime is not None:
            unsupported: tuple[tuple[bool, str, str, str, str], ...] = (
                (
                    bool(spec.python_tools),
                    "python_tools",
                    "codex_python_tools_unsupported",
                    "当前运行时尚未接通 Studio 自定义算子，请移除后发布",
                    "pythonTools",
                ),
                (
                    bool(spec.knowledge_references),
                    "knowledge",
                    "codex_knowledge_unsupported",
                    "当前运行时尚未接通 Studio Knowledge，请移除后发布",
                    "knowledgeReferences",
                ),
                (
                    spec.tool_exposure_mode == "on_demand",
                    "tool_search",
                    "codex_tool_search_unsupported",
                    "当前运行时尚未接通 Studio 按需工具加载，请改为启动时加载",
                    "toolExposureMode",
                ),
                (
                    bool(spec.subagents) or "Task" in spec.builtin_tools,
                    "subagents",
                    "runtime_subagents_unsupported",
                    "当前运行时尚未接通 Studio Sub Agent",
                    "subagents",
                ),
            )
            issues.extend(
                ValidationIssue(
                    code=(
                        legacy_code
                        if spec.runtime == "codex-app-server" and legacy_code.startswith("codex_")
                        else f"runtime_{feature}_unsupported"
                    ),
                    message=f"{runtime.label}：{message}",
                    severity=ValidationSeverity.ERROR,
                    path=path,
                )
                for enabled, feature, legacy_code, message, path in unsupported
                if enabled and feature not in runtime_features
            )
        mcp_servers = {server.reference: server for server in self._catalog.mcp_servers}
        if spec.python_tools and spec.tool_exposure_mode == "on_demand":
            issues.append(
                ValidationIssue(
                    code="python_tool_on_demand_unsupported",
                    message="自定义算子仅支持启动时加载",
                    severity=ValidationSeverity.ERROR,
                    path="pythonTools",
                )
            )
        if spec.tool_exposure_mode == "on_demand" and not spec.mcp_servers:
            issues.append(
                ValidationIssue(
                    code="tool_search_without_mcp",
                    message="按需工具加载至少需要一个 MCP 工具源",
                    severity=ValidationSeverity.ERROR,
                    path="toolExposureMode",
                )
            )
        for reference in spec.mcp_servers:
            server = mcp_servers.get(reference)
            if server is None:
                issues.append(
                    ValidationIssue(
                        code="mcp_server_unknown",
                        message=f"MCP 能力未注册：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="mcpServers",
                    )
                )
            elif not server.enabled:
                issues.append(
                    ValidationIssue(
                        code="mcp_server_disabled",
                        message=f"MCP 能力已禁用：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="mcpServers",
                    )
                )
            elif runtime is not None and f"mcp_{server.transport}" not in runtime_features:
                issues.append(
                    ValidationIssue(
                        code="runtime_mcp_transport_unsupported",
                        message=(
                            f"{runtime.label} 不支持 MCP {server.transport} transport：{reference}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="mcpServers",
                        relatedReferences=(reference,),
                    )
                )
        skill_capabilities = {item.package_id: item for item in self._catalog.skills}
        platform_packages = {
            package.package_id: package
            for package in default_platform_skill_catalog().packages
        }
        for reference in spec.skill_references:
            capability = skill_capabilities.get(reference)
            if capability is None:
                issues.append(
                    ValidationIssue(
                        code="skill_reference_unknown",
                        message=f"目录 Skill 未注册：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="skillReferences",
                        relatedReferences=(reference,),
                    )
                )
                continue
            if not capability.enabled:
                issues.append(
                    ValidationIssue(
                        code="skill_reference_disabled",
                        message=f"目录 Skill 已禁用：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="skillReferences",
                        relatedReferences=(reference,),
                    )
                )
                continue
            package = platform_packages.get(reference)
            if package is None:
                issues.append(
                    ValidationIssue(
                        code="skill_reference_unavailable",
                        message=f"平台 Skill 包不存在：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="skillReferences",
                        relatedReferences=(reference,),
                    )
                )
            elif spec.runtime not in package.compatible_runtimes:
                issues.append(
                    ValidationIssue(
                        code="skill_reference_runtime_incompatible",
                        message=(
                            f"{spec.runtime} 运行时不兼容目录 Skill：{reference}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="skillReferences",
                        relatedReferences=(reference,),
                    )
                )
            elif any(skill.name == reference for skill in spec.skills):
                issues.append(
                    ValidationIssue(
                        code="skill_reference_conflicts_snapshot",
                        message=(
                            f"已存在同名 Skill 快照，目录引用重复：{reference}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="skillReferences",
                        relatedReferences=(reference,),
                    )
                )
        policies = {policy.policy_id: policy for policy in self._catalog.policies}
        policy = policies.get(spec.permission_policy)
        if policy is None:
            issues.append(
                ValidationIssue(
                    code="policy_unknown",
                    message=f"权限 Profile 未注册：{spec.permission_policy}",
                    severity=ValidationSeverity.ERROR,
                    path="permissionPolicy",
                )
            )
        elif not policy.enabled:
            issues.append(
                ValidationIssue(
                    code="policy_disabled",
                    message=f"权限 Profile 已禁用：{spec.permission_policy}",
                    severity=ValidationSeverity.ERROR,
                    path="permissionPolicy",
                )
            )
        profiles = {profile.profile_id: profile for profile in self._catalog.execution_profiles}
        profile = profiles.get(spec.execution_profile)
        if profile is None:
            issues.append(
                ValidationIssue(
                    code="execution_profile_unknown",
                    message=f"执行 Profile 未注册：{spec.execution_profile}",
                    severity=ValidationSeverity.ERROR,
                    path="executionProfile",
                )
            )
        elif not profile.enabled:
            issues.append(
                ValidationIssue(
                    code="execution_profile_disabled",
                    message=f"执行 Profile 已禁用：{spec.execution_profile}",
                    severity=ValidationSeverity.ERROR,
                    path="executionProfile",
                )
            )
        else:
            selected_mcp = {
                reference: server
                for reference in spec.mcp_servers
                if (server := mcp_servers.get(reference)) is not None and server.enabled
            }

            def compatible_profiles(*, production_only: bool) -> tuple[str, ...]:
                return tuple(
                    candidate.profile_id
                    for candidate in self._catalog.execution_profiles
                    if candidate.enabled
                    and (not production_only or candidate.production_allowed)
                    and all(
                        server.network_access in candidate.network_access
                        and reference in candidate.allowed_mcp_references
                        for reference, server in selected_mcp.items()
                    )
                )

            incompatible_network = tuple(
                sorted(
                    reference
                    for reference, server in selected_mcp.items()
                    if server.network_access not in profile.network_access
                )
            )
            if incompatible_network:
                requirements = ", ".join(
                    f"{reference}（{selected_mcp[reference].network_access.value}）"
                    for reference in incompatible_network
                )
                issues.append(
                    ValidationIssue(
                        code="execution_profile_network_incompatible",
                        message=(
                            f"执行 Profile {profile.profile_id} 缺少 MCP 所需网络级别："
                            f"{requirements}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="executionProfile",
                        relatedReferences=incompatible_network,
                        suggestedProfileIds=compatible_profiles(production_only=False),
                    )
                )

            incompatible_egress = tuple(
                sorted(set(selected_mcp).difference(profile.allowed_mcp_references))
            )
            if incompatible_egress:
                issues.append(
                    ValidationIssue(
                        code="execution_profile_egress_incompatible",
                        message=(
                            f"执行 Profile {profile.profile_id} 的 Egress Policy 未授权 MCP："
                            f"{', '.join(incompatible_egress)}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="executionProfile",
                        relatedReferences=incompatible_egress,
                        suggestedProfileIds=compatible_profiles(production_only=False),
                    )
                )

            if not profile.production_allowed:
                production_profiles = compatible_profiles(production_only=True)
                recommendation = (
                    f"；可切换至 {', '.join(production_profiles)}"
                    if production_profiles
                    else "；当前没有同时满足所选 MCP 的生产 Profile"
                )
                issues.append(
                    ValidationIssue(
                        code="execution_profile_preview_only",
                        message=(
                            f"执行 Profile {profile.profile_id} 仅用于 Preview，不能部署到生产"
                            f"{recommendation}"
                        ),
                        severity=ValidationSeverity.ERROR,
                        path="executionProfile",
                        stage=ValidationStage.PRODUCTION,
                        relatedReferences=(profile.profile_id,),
                        suggestedProfileIds=production_profiles,
                    )
                )
        return tuple(issues)

    def _deployment_warnings(self, draft: AgentDraft) -> tuple[ValidationIssue, ...]:
        mcp_by_reference = {item.reference: item for item in self._catalog.mcp_servers}
        warnings = [
            ValidationIssue(
                code="mcp_deployment_preflight_required",
                message=(
                    f"发布部署前需从实际 Sandbox 校验 {reference} 的凭据、"
                    "MCP tools/list 和网络可达性"
                ),
                severity=ValidationSeverity.WARNING,
                path="mcpServers",
                stage=ValidationStage.PRODUCTION,
                relatedReferences=(reference,),
            )
            for reference in draft.spec.mcp_servers
            if (capability := mcp_by_reference.get(reference)) is not None
            and capability.preflight_required
        ]
        return tuple(warnings)

    def tool_directory(self, draft: AgentDraft) -> ToolDirectorySnapshot:
        builtin_by_name = {item.name: item for item in self._catalog.builtin_tools}
        mcp_by_reference = {item.reference: item for item in self._catalog.mcp_servers}
        entries: list[ToolDirectoryEntry] = []
        for name in draft.spec.builtin_tools:
            capability = builtin_by_name.get(name)
            if capability is None:
                continue
            entries.append(
                ToolDirectoryEntry(
                    name=name,
                    source="builtin",
                    logicalReference=name,
                    description=capability.description,
                    risk=capability.risk.value,
                    resultTrust="untrusted" if name in {"WebSearch", "WebFetch"} else "safe",
                )
            )
        for reference in draft.spec.mcp_servers:
            capability = mcp_by_reference.get(reference)
            if capability is None:
                continue
            result_trust = (
                "untrusted"
                if capability.network_access is NetworkAccess.EXTERNAL or capability.sends_user_data
                else "sensitive"
            )
            for name in capability.tools:
                entries.append(
                    ToolDirectoryEntry(
                        name=name,
                        source="mcp",
                        logicalReference=reference,
                        description=(
                            f"{capability.description} Reviewed tool: {name.rsplit('__', 1)[-1]}."
                        ),
                        risk=capability.risk.value,
                        resultTrust=result_trust,
                    )
                )
        for tool in draft.spec.python_tools:
            reference = f"bundle:tools/{tool.name}.py"
            entries.append(
                ToolDirectoryEntry(
                    name=(f"mcp__harness-python-{draft.spec.name}__{tool.name}"),
                    source="python",
                    logicalReference=reference,
                    description=tool.description,
                    risk="high",
                    resultTrust="safe",
                )
            )
        return ToolDirectorySnapshot.create(
            catalog_revision=self._catalog_revision,
            exposure_mode=draft.spec.tool_exposure_mode,
            entries=entries,
        )

    def _materialize(self, draft: AgentDraft, root: Path, manifest_yaml: str) -> Path:
        spec = draft.spec
        prompt = root / "prompts" / "system.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(spec.system_prompt, encoding="utf-8")

        for skill in self.resolve_skills(draft):
            skill_root = root / "skills" / skill.name
            skill_root.mkdir(parents=True, exist_ok=True)
            frontmatter_payload: dict[str, object] = {
                "name": skill.name,
                "description": skill.description,
            }
            if skill.source is not None:
                frontmatter_payload["metadata"] = {
                    "harness": {
                        "source": skill.source.model_dump(mode="json", by_alias=True),
                    }
                }
            frontmatter = yaml.safe_dump(
                frontmatter_payload,
                sort_keys=False,
                allow_unicode=True,
            ).strip()
            (skill_root / "SKILL.md").write_text(
                f"---\n{frontmatter}\n---\n\n{skill.instructions.strip()}\n",
                encoding="utf-8",
            )
            for file in skill.files:
                target = skill_root.joinpath(*Path(file.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if file.content is not None:
                    target.write_text(file.content, encoding="utf-8")
                else:
                    target.write_bytes(base64.b64decode(file.content_base64 or "", validate=True))

        tools_root = root / "tools"
        for tool in spec.python_tools:
            tools_root.mkdir(parents=True, exist_ok=True)
            metadata = pprint.pformat(
                {
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "name": tool.name,
                },
                sort_dicts=True,
                width=100,
            )
            source = _python_tool_source(tool.code, metadata)
            (tools_root / f"{tool.name}.py").write_text(source, encoding="utf-8")

        eval_path = root / "evals" / "suite.yaml"
        eval_path.parent.mkdir(parents=True, exist_ok=True)
        suite = EvalSuite(
            apiVersion="harness/v1alpha1",
            kind="EvalSuite",
            agent=spec.name,
            cases=spec.evaluation_cases,
        )
        eval_path.write_text(
            yaml.safe_dump(
                suite.model_dump(mode="json", by_alias=True, exclude_none=True),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            f"# {spec.display_name}\n\n{spec.description}\n",
            encoding="utf-8",
        )
        (root / STUDIO_BUNDLE_METADATA_FILENAME).write_text(
            json.dumps(
                StudioBundleMetadata(
                    apiVersion="harness.studio/v1",
                    kind="AgentDraftMetadata",
                    description=spec.description,
                    taskContract=spec.task_contract,
                    executionProfile=spec.execution_profile,
                ).model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / TOOL_DIRECTORY_FILENAME).write_text(
            json.dumps(
                self.tool_directory(draft).model_dump(
                    mode="json",
                    by_alias=True,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest = root / "agent.yaml"
        manifest.write_text(manifest_yaml, encoding="utf-8")
        return manifest
