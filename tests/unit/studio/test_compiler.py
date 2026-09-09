import base64
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import yaml

from harness.agent_package import extract_agent_bundle, pack_agent_package
from harness.core.manifest import ToolDirectorySnapshot
from harness.studio.bundle_format import StudioBundleMetadata
from harness.studio.bundle_import import AgentBundleImportError, parse_agent_bundle
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.factory import create_draft_spec
from harness.studio.models import (
    AgentDraft,
    AgentDraftSpec,
    AgentTemplate,
    CapabilityRisk,
    DraftPythonTool,
    DraftSkill,
    DraftSkillFile,
    DraftSubagent,
    McpCapability,
    ModelRouteCapability,
    NetworkAccess,
    ValidationSeverity,
)
from harness.studio.nexau_export import export_nexau_agent
from harness.studio.platform_skills import platform_skill_package

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def draft(template: AgentTemplate = AgentTemplate.ANALYST) -> AgentDraft:
    return AgentDraft(
        draftId="draft_test",
        tenantId="tenant-a",
        revision=1,
        spec=create_draft_spec(
            name="invoice-reviewer",
            domain="accounts-payable",
            display_name="发票审核助手",
            description="核对发票证据并标记需要人工确认的例外。",
            template=template,
        ),
        createdBy="builder-a",
        updatedBy="builder-a",
        createdAt=NOW,
        updatedAt=NOW,
    )


def test_default_draft_compiles_to_existing_reproducible_bundle_contract() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())

    validation = compiler.validate(draft())
    compiled = compiler.compile(draft())

    assert validation.ready is True
    assert validation.content_hash == compiled.report.snapshot.content_hash
    assert validation.package_hash == compiled.report.package_hash
    assert compiled.filename.startswith("invoice-reviewer-0.1.0-")
    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        names = set(bundle.namelist())
        assert {
            "agent.yaml",
            "bundle.json",
            "studio.json",
            "prompts/system.md",
            "evals/suite.yaml",
            "tool-directory.json",
        }.issubset(names)
        manifest = bundle.read("agent.yaml").decode()
        directory = ToolDirectorySnapshot.model_validate_json(bundle.read("tool-directory.json"))
        studio_metadata = StudioBundleMetadata.model_validate_json(bundle.read("studio.json"))
    assert "route: deepseek-v4-pro" in manifest
    assert "runtime: claude-agent-sdk" in manifest
    assert "mode: isolated" in manifest
    assert directory.exposure_mode == "eager"
    assert directory.catalog_revision == 1
    assert studio_metadata.description == draft().spec.description
    assert studio_metadata.execution_profile == draft().spec.execution_profile
    assert {entry.name for entry in directory.entries} == set(draft().spec.builtin_tools)


def test_platform_skill_provenance_round_trips_through_the_immutable_bundle() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    source = draft()
    package = platform_skill_package("evidence-reporting", 1)
    source = source.model_copy(
        update={
            "spec": source.spec.model_copy(
                update={
                    "skills": (package.skill,),
                    "evaluation_cases": (
                        *source.spec.evaluation_cases,
                        *package.evaluation_cases,
                    ),
                }
            )
        }
    )

    imported = parse_agent_bundle(compiler.compile(source).bundle)

    assert imported.spec.skills[0].source == package.skill.source
    assert imported.spec.skills[0].source is not None
    assert imported.spec.skills[0].source.modified is False
    assert any("skill:evidence-reporting" in case.tags for case in imported.spec.evaluation_cases)


def test_codex_runtime_compiles_and_round_trips_with_a_responses_route() -> None:
    catalog = default_capability_catalog()
    responses_route = ModelRouteCapability(
        routeId="codex-deepseek-v4-flash",
        label="Codex DeepSeek V4 Flash",
        provider="new-api",
        models=("deepseek-v4-flash",),
        capabilities=("streaming", "tool_use"),
        apiFormat="openai_compatible",
        credentialReference="CODEX_GATEWAY_KEY",
    )
    compiler = AgentDraftCompiler(
        catalog.model_copy(update={"model_routes": (*catalog.model_routes, responses_route)})
    )
    source = draft()
    source = source.model_copy(
        update={
            "spec": source.spec.model_copy(
                update={
                    "runtime": "codex-app-server",
                    "model": source.spec.model.model_copy(
                        update={
                            "route_id": responses_route.route_id,
                            "model": "deepseek-v4-flash",
                            "reasoning_effort": "low",
                        }
                    ),
                    "mcp_servers": ("tavily-readonly",),
                }
            )
        }
    )

    compiled = compiler.compile(source)
    imported = parse_agent_bundle(compiled.bundle)

    assert "runtime: codex-app-server" in compiled.manifest_yaml
    assert "codex-reasoning-effort: low" in compiled.manifest_yaml
    assert imported.spec.runtime == "codex-app-server"
    assert imported.spec.model.route_id == responses_route.route_id
    assert imported.spec.model.reasoning_effort == "low"
    assert imported.spec.mcp_servers == ("tavily-readonly",)


def test_codex_runtime_rejects_capabilities_other_than_http_mcp() -> None:
    catalog = default_capability_catalog()
    responses_route = ModelRouteCapability(
        routeId="codex-deepseek-v4-flash",
        label="Codex DeepSeek V4 Flash",
        provider="new-api",
        models=("deepseek-v4-flash",),
        capabilities=("streaming", "tool_use", "tool_search"),
        apiFormat="openai_compatible",
        credentialReference="CODEX_GATEWAY_KEY",
    )
    compiler = AgentDraftCompiler(
        catalog.model_copy(update={"model_routes": (*catalog.model_routes, responses_route)})
    )
    source = draft()
    python_tool = DraftPythonTool(
        name="normalize_score",
        description="Normalize a score.",
        inputSchema={"type": "object", "properties": {}},
        code="def run(arguments):\n    return {'ok': True}\n",
    )
    source = source.model_copy(
        update={
            "spec": source.spec.model_copy(
                update={
                    "runtime": "codex-app-server",
                    "model": source.spec.model.model_copy(
                        update={
                            "route_id": responses_route.route_id,
                            "model": "deepseek-v4-flash",
                        }
                    ),
                    "builtin_tools": (*source.spec.builtin_tools, "Task"),
                    "python_tools": (python_tool,),
                    "mcp_servers": ("tavily-readonly",),
                    "knowledge_references": ("company-policy",),
                    "subagents": (
                        DraftSubagent(
                            alias="fact-checker",
                            ref="helper-agent@1.0.0",
                            responsibility="核验事实。",
                        ),
                    ),
                    "tool_exposure_mode": "on_demand",
                }
            )
        }
    )

    validation = compiler.validate(source)
    codes = {issue.code for issue in validation.issues}

    assert validation.ready is False
    assert {
        "codex_python_tools_unsupported",
        "codex_knowledge_unsupported",
        "codex_tool_search_unsupported",
    }.issubset(codes)
    assert "codex_subagents_unsupported" not in codes
    assert "codex_mcp_unsupported" not in codes


def test_codex_runtime_rejects_anthropic_route() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    source = draft()
    source = source.model_copy(
        update={"spec": source.spec.model_copy(update={"runtime": "codex-app-server"})}
    )

    validation = compiler.validate(source)

    assert validation.ready is False
    assert any(issue.code == "codex_responses_route_required" for issue in validation.issues)


def test_binary_skill_asset_survives_compile_and_studio_round_trip() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    source = draft()
    payload = b"\x89PNG\r\n\x1a\n\x00\xff"
    skill = DraftSkill(
        name="invoice-reviewer-core",
        description="Review invoice evidence.",
        instructions="Verify the invoice before reporting a result.",
        files=(
            DraftSkillFile(
                path="assets/template.png",
                contentBase64=base64.b64encode(payload).decode("ascii"),
            ),
        ),
    )
    source = source.model_copy(update={"spec": source.spec.model_copy(update={"skills": (skill,)})})

    compiled = compiler.compile(source)
    imported = parse_agent_bundle(compiled.bundle)

    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        assert bundle.read("skills/invoice-reviewer-core/assets/template.png") == payload
    imported_asset = imported.spec.skills[0].files[0]
    assert imported_asset.content is None
    assert imported_asset.content_base64 == base64.b64encode(payload).decode("ascii")


def test_nexau_export_is_deterministic_and_round_trips_editable_assets() -> None:
    source = draft()
    payload = b"\x89PNG\r\n\x1a\n\x00\xff"
    skill = DraftSkill(
        name="invoice-reviewer-core",
        description="Review invoice evidence.",
        instructions="Verify the invoice before reporting a result.",
        files=(
            DraftSkillFile(
                path="assets/template.png",
                contentBase64=base64.b64encode(payload).decode("ascii"),
            ),
        ),
    )
    python_tool = DraftPythonTool(
        name="normalize_score",
        description="Normalize a score.",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
        },
        code=(
            "def run(arguments):\n"
            "    value = float(arguments['value'])\n"
            "    return {'normalized': max(0, min(1, value))}\n"
        ),
    )
    source = source.model_copy(
        update={
            "spec": source.spec.model_copy(
                update={
                    "skills": (skill,),
                    "python_tools": (python_tool,),
                    "builtin_tools": (*source.spec.builtin_tools, "Edit", "Task"),
                    "mcp_servers": ("tavily-readonly",),
                    "subagents": (
                        DraftSubagent(
                            alias="fact-researcher",
                            ref="helper-agent@1.0.0",
                            responsibility="只读核验事实、来源和证据缺口。",
                            background=True,
                        ),
                    ),
                }
            )
        }
    )

    tavily = McpCapability(
        reference="tavily-readonly",
        serverName="tavily",
        label="公网搜索",
        description="只读公网搜索。",
        endpointUrl="https://mcp.tavily.com/mcp/",
        tools=("mcp__tavily__tavily_search",),
        risk=CapabilityRisk.MEDIUM,
        networkAccess=NetworkAccess.EXTERNAL,
        sendsUserData=True,
        readOnly=True,
        executionLocation="external-mcp",
        credentialReference="TAVILY_API_KEY",
        authMode="query",
        authName="tavilyApiKey",
        authKey="api_key",
    )

    first = export_nexau_agent(
        source,
        mcp_capabilities={"tavily-readonly": tavily},
    )
    second = export_nexau_agent(
        source,
        mcp_capabilities={"tavily-readonly": tavily},
    )

    assert first.content == second.content
    assert first.filename == "invoice-reviewer-0.1.0-nexau.zip"
    with ZipFile(BytesIO(first.content)) as archive:
        manifest = json.loads(archive.read("nexau.json"))
        assert manifest == {
            "agents": {"invoice-reviewer": "agent.yaml"},
            "excluded": [".nexau/", ".env", "__pycache__/"],
        }
        config = yaml.safe_load(archive.read("agent.yaml"))
        assert config["type"] == "agent"
        assert manifest["agents"] == {config["name"]: "agent.yaml"}
        assert config["system_prompt"] == "./systemprompt.md"
        assert config["tool_call_mode"] == "structured"
        assert config["max_context_tokens"] == 128000
        assert config["llm_config"]["model"] == "${env.LLM_MODEL}"
        assert config["skills"] == ["./skills/invoice-reviewer-core"]
        assert "tracers" not in config
        assert "harness_extensions" not in config
        extensions = json.loads(archive.read("agent-studio.json"))
        assert extensions["unmapped_builtin_tools"] == []
        assert {tool["name"] for tool in config["tools"]} == {
            "read_file",
            "write_file",
            "list_directory",
            "search_file_content",
            "replace",
            "run_shell_command",
            "normalize_score",
        }
        assert config["mcp_servers"] == [
            {
                "name": "tavily",
                "source_id": "tavily-readonly",
                "type": "http",
                "url": "https://mcp.tavily.com/mcp/?tavilyApiKey=${env.TAVILY_API_KEY}",
                "timeout": 30,
            }
        ]
        assert config["sub_agents"] == [
            {
                "name": "fact-researcher",
                "config_path": "./subagents/fact-researcher/agent.yaml",
                "source_id": "helper-agent@1.0.0",
            }
        ]
        assert archive.read("skills/invoice-reviewer-core/assets/template.png") == payload
        assert "tools/search_file_content.tool.yaml" in archive.namelist()
        assert "tools/replace.tool.yaml" in archive.namelist()
        assert "custom_tools/normalize_score.py" in archive.namelist()
        assert "subagents/fact-researcher/agent.yaml" in archive.namelist()
        assert "subagents/fact-researcher/systemprompt.md" in archive.namelist()
        assert "NAC-DEPLOYMENT.md" in archive.namelist()
        assert "skills 根目录位于 /home/user/.skills/" in archive.read("systemprompt.md").decode()
        deployment_guide = archive.read("NAC-DEPLOYMENT.md").decode()
        assert "`LLM_MODEL`" in deployment_guide
        subagent_config = yaml.safe_load(archive.read("subagents/fact-researcher/agent.yaml"))
        assert subagent_config["llm_config"]["model"] == "${env.LLM_MODEL}"
        assert subagent_config["skills"] == []

    imported = parse_agent_bundle(first.content)
    assert imported.spec.mcp_servers == ("tavily-readonly",)
    assert imported.spec.subagents[0].alias == "fact-researcher"
    assert {"Grep", "Edit", "Task"}.issubset(imported.spec.builtin_tools)
    namespace: dict[str, object] = {}
    exec(imported.spec.python_tools[0].code, namespace)
    result = namespace["run"]({"value": 1.4})  # type: ignore[operator]
    assert result == {"normalized": 1}


def test_studio_bundle_round_trips_into_an_editable_spec() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    source = draft()
    compiled = compiler.compile(source)

    imported = parse_agent_bundle(compiled.bundle)
    rebuilt = source.model_copy(update={"spec": imported.spec})
    rebuilt_bundle = compiler.compile(rebuilt)

    assert imported.lossless is True
    assert imported.warnings == ()
    assert imported.spec.name == source.spec.name
    assert imported.spec.description == source.spec.description
    assert imported.spec.execution_profile == source.spec.execution_profile
    assert imported.spec.system_prompt == source.spec.system_prompt
    assert imported.spec.skills == source.spec.skills
    assert rebuilt_bundle.report.package_hash == compiled.report.package_hash


def test_bundle_python_tool_round_trips_with_source_and_directory_contract() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    source = draft()
    python_tool = DraftPythonTool(
        name="normalize_score",
        description="Normalize a score inside the isolated sandbox.",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        code=(
            "def run(arguments):\n"
            "    value = float(arguments['value'])\n"
            "    return {'normalized': max(0, min(1, value))}\n"
        ),
    )
    source = source.model_copy(
        update={"spec": source.spec.model_copy(update={"python_tools": (python_tool,)})}
    )

    compiled = compiler.compile(source)
    imported = parse_agent_bundle(compiled.bundle)
    rebuilt = source.model_copy(update={"spec": imported.spec})
    rebuilt_bundle = compiler.compile(rebuilt)

    assert imported.spec.python_tools == (python_tool,)
    assert rebuilt_bundle.report.package_hash == compiled.report.package_hash
    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        assert "tools/normalize_score.py" in bundle.namelist()
        directory = ToolDirectorySnapshot.model_validate_json(bundle.read("tool-directory.json"))
    python_entry = next(entry for entry in directory.entries if entry.source == "python")
    assert python_entry.name == ("mcp__harness-python-invoice-reviewer__normalize_score")
    assert python_entry.logical_reference == "bundle:tools/normalize_score.py"


def test_bundle_python_tool_keeps_future_import_before_generated_metadata() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    source = draft()
    python_tool = DraftPythonTool(
        name="future_safe",
        description="Exercise a module future import.",
        inputSchema={"type": "object", "properties": {}},
        code=(
            '"""Imported operator."""\n\n'
            "from __future__ import annotations\n\n"
            "def run(arguments):\n"
            "    return {'ok': True}\n"
        ),
    )
    source = source.model_copy(
        update={"spec": source.spec.model_copy(update={"python_tools": (python_tool,)})}
    )

    compiled = compiler.compile(source)
    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        generated = bundle.read("tools/future_safe.py").decode()

    compile(generated, "tools/future_safe.py", "exec")
    assert generated.index("from __future__ import annotations") < generated.index("TOOL_SPEC")


def test_legacy_bundle_import_is_compatible_but_not_marked_lossless() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    compiled = compiler.compile(draft())
    with TemporaryDirectory() as directory:
        root = Path(directory)
        manifest, _content_hash, _package_hash = extract_agent_bundle(
            compiled.bundle,
            destination=root / "source",
        )
        (manifest.parent / "studio.json").unlink()
        archive, _report = pack_agent_package(
            manifest,
            output_directory=root / "dist",
        )
        imported = parse_agent_bundle(archive.read_bytes())

    assert imported.lossless is False
    assert imported.spec.description == draft().spec.description
    assert imported.spec.execution_profile == "isolated-default"
    assert any("旧 Bundle 不含 studio.json" in warning for warning in imported.warnings)


def test_nexau_export_imports_python_bindings_skills_and_unlimited_runtime() -> None:
    archive = BytesIO()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "agent.yaml",
            yaml.safe_dump(
                {
                    "type": "agent",
                    "name": "image_detector",
                    "description": "图像检测智能体",
                    "system_prompt": "./systemprompt.md",
                    "max_iterations": 300,
                    "max_context_tokens": 128000,
                    "llm_config": {"model": "${env.LLM_MODEL}"},
                    "tools": [
                        {
                            "name": "read_visual_file",
                            "binding": "nexau.archs.tool.builtin.file_tools:read_visual_file",
                        },
                        {
                            "name": "read_file",
                            "binding": "nexau.archs.tool.builtin.file_tools:read_file",
                        },
                        {
                            "name": "score_detections",
                            "yaml_path": "tools/score.tool.yaml",
                            "binding": "custom_tools.detection:score_detections",
                        },
                    ],
                    "skills": ["./skills/grid-system", "./skills/检测规则"],
                },
                allow_unicode=True,
            ),
        )
        bundle.writestr("systemprompt.md", "# Mission\n\nDo the work.\n")
        bundle.writestr(
            "tools/score.tool.yaml",
            yaml.safe_dump(
                {
                    "type": "tool",
                    "name": "score_detections",
                    "description": "Score detections.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"value": {"type": "number"}},
                        "required": ["value"],
                    },
                }
            ),
        )
        bundle.writestr(
            "custom_tools/detection.py",
            "def score_detections(value):\n    return {'score': value}\n",
        )
        bundle.writestr(
            "skills/grid-system/SKILL.md",
            "---\nname: 网格系统\ndescription: Grid workflow.\n---\n\n# Grid\n\nUse a grid.\n",
        )
        bundle.writestr(
            "skills/grid-system/scripts/grid.py",
            "output = '/tmp/detection_output/grid.jpg'\n",
        )
        bundle.writestr(
            "skills/检测规则/SKILL.md",
            "---\nname: 检测规则\ndescription: Detection rules.\n---\n\n# Rules\n\nApply rules.\n",
        )

    imported = parse_agent_bundle(archive.getvalue())

    assert imported.lossless is False
    assert imported.spec.builtin_tools == ("Read",)
    assert imported.spec.model.route_id == "minimax-m3"
    assert imported.spec.model.model == "MiniMax-M3"
    assert "vision" in imported.spec.model.required_capabilities
    assert [item.name for item in imported.spec.python_tools] == ["score_detections"]
    assert "def run(arguments)" in imported.spec.python_tools[0].code
    assert imported.spec.skills[0].name == "grid-system"
    assert imported.spec.skills[0].files[0].path == "scripts/grid.py"
    assert imported.spec.skills[0].files[0].content is not None
    assert "outputs/detection_output/grid.jpg" in imported.spec.skills[0].files[0].content
    assert imported.spec.skills[1].name.startswith("imported-skill-")
    assert imported.spec.limits.max_turns is None
    assert imported.spec.limits.max_model_tokens is None
    assert imported.spec.limits.max_subagent_usage_units is None
    assert any("不设硬上限" in warning for warning in imported.warnings)


def test_studio_bundle_rejects_metadata_that_disagrees_with_readme() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    compiled = compiler.compile(draft())
    with TemporaryDirectory() as directory:
        root = Path(directory)
        manifest, _content_hash, _package_hash = extract_agent_bundle(
            compiled.bundle,
            destination=root / "source",
        )
        (manifest.parent / "README.md").write_text(
            "# 被篡改的说明\n\n内容不一致。\n",
            encoding="utf-8",
        )
        archive, _report = pack_agent_package(
            manifest,
            output_directory=root / "dist",
        )

        try:
            parse_agent_bundle(archive.read_bytes())
        except AgentBundleImportError as error:
            assert "README.md" in str(error)
        else:
            raise AssertionError("Expected inconsistent Studio metadata to be rejected")


def test_missing_eval_coverage_has_a_stable_actionable_issue() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    current = draft()
    without_ambiguous = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "evaluation_cases": tuple(
                        case
                        for case in current.spec.evaluation_cases
                        if "ambiguous" not in case.tags
                    )
                }
            )
        }
    )

    validation = compiler.validate(without_ambiguous)

    issue = next(
        item for item in validation.issues if item.code == "evaluation_coverage_ambiguous_missing"
    )
    assert validation.ready is False
    assert issue.path == "evaluationCases"
    assert issue.message == "evaluation suite is missing ambiguous coverage"


def test_disabled_eval_does_not_block_bundle_coverage() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    current = draft()
    disabled = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "evaluation_enabled": False,
                    "evaluation_cases": (current.spec.evaluation_cases[0],),
                }
            )
        }
    )

    validation = compiler.validate(disabled)
    compiled = compiler.compile(disabled)

    assert validation.ready is True
    manifest = yaml.safe_load(compiled.manifest_yaml)
    assert manifest["metadata"]["labels"]["evaluation-enabled"] == "false"


def test_knowledge_references_are_pinned_in_manifest_and_effective_contract() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    current = draft()
    with_knowledge = current.model_copy(
        update={
            "spec": current.spec.model_copy(update={"knowledge_references": ("company-policy",)})
        }
    )

    validation = compiler.validate(with_knowledge)
    compiled = compiler.compile(with_knowledge)

    assert validation.ready is True
    assert validation.contract.knowledge_references == ("company-policy",)
    assert "knowledgeReferences:" in validation.manifest_yaml
    assert "- company-policy" in validation.manifest_yaml
    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        manifest = bundle.read("agent.yaml").decode()
    assert "knowledgeReferences:" in manifest
    assert "- company-policy" in manifest


def test_tavily_is_a_controlled_external_mcp_capability_not_general_network() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    current = draft()
    enabled = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "builtin_tools": ("Read", "Glob", "Grep", "Write"),
                    "mcp_servers": ("tavily-readonly",),
                }
            )
        }
    )

    validation = compiler.validate(enabled)

    assert validation.ready is True
    assert validation.contract.network_access is NetworkAccess.EXTERNAL
    assert validation.contract.network_summary == "仅通过审核过的外部 MCP 受控联网"
    assert validation.contract.risk is CapabilityRisk.MEDIUM
    assert any(
        issue.code == "mcp_deployment_preflight_required"
        and issue.severity is ValidationSeverity.WARNING
        for issue in validation.issues
    )
    assert "mcp: tavily-readonly" in validation.manifest_yaml


def test_on_demand_bundle_pins_reviewed_tool_directory_and_route_capability() -> None:
    catalog = default_capability_catalog()
    catalog = catalog.model_copy(
        update={
            "model_routes": (
                *catalog.model_routes,
                ModelRouteCapability(
                    routeId="on-demand-test",
                    label="On-demand test route",
                    provider="test",
                    models=("deepseek-v4-pro",),
                    capabilities=("streaming", "tool_use", "tool_search"),
                    credentialReference="NEW_API_KEY",
                ),
            )
        }
    )
    compiler = AgentDraftCompiler(
        catalog,
        catalog_revision=9,
    )
    current = draft()
    on_demand = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "model": current.spec.model.model_copy(
                        update={
                            "route_id": "on-demand-test",
                            "model": "deepseek-v4-pro",
                            "required_capabilities": (
                                "streaming",
                                "tool_use",
                                "tool_search",
                            ),
                        }
                    ),
                    "mcp_servers": ("tavily-readonly",),
                    "tool_exposure_mode": "on_demand",
                }
            )
        }
    )

    validation = compiler.validate(on_demand)
    compiled = compiler.compile(on_demand)

    assert validation.ready is True
    assert validation.contract.tool_exposure_mode == "on_demand"
    assert validation.contract.tool_directory_entries == 7
    assert "toolExposureMode: on_demand" in validation.manifest_yaml
    assert "tool_search" in validation.manifest_yaml
    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        raw = json.loads(bundle.read("tool-directory.json"))
        directory = ToolDirectorySnapshot.model_validate(raw)
    assert directory.catalog_revision == 9
    assert directory.exposure_mode == "on_demand"
    assert directory.content_hash == directory.digest()
    assert {entry.name for entry in directory.entries if entry.source == "mcp"} == {
        "mcp__tavily__tavily_search",
        "mcp__tavily__tavily_extract",
    }
    assert {entry.logical_reference for entry in directory.entries if entry.source == "mcp"} == {
        "tavily-readonly"
    }


def test_on_demand_mode_fails_closed_on_route_without_tool_search() -> None:
    current = draft()
    unsupported = current.model_copy(
        update={"spec": current.spec.model_copy(update={"tool_exposure_mode": "on_demand"})}
    )

    validation = AgentDraftCompiler(default_capability_catalog()).validate(unsupported)

    assert validation.ready is False
    assert "tool_search_capability_missing" in {issue.code for issue in validation.issues}


def test_unknown_model_tool_and_mcp_fail_closed_before_packaging() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    current = draft()
    invalid = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "model": current.spec.model.model_copy(update={"route_id": "unreviewed-route"}),
                    "builtin_tools": (*current.spec.builtin_tools, "DangerousTool"),
                    "mcp_servers": ("arbitrary-url",),
                }
            )
        }
    )

    validation = compiler.validate(invalid)

    assert validation.ready is False
    assert {issue.code for issue in validation.issues} == {
        "model_route_unknown",
        "builtin_tool_unknown",
        "mcp_server_unknown",
    }
    assert validation.package_hash is None


def test_sandbox_is_mandatory_and_provider_is_not_authored_by_domain_agent() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    operator = draft(AgentTemplate.OPERATOR)

    contract = compiler.effective_contract(operator)

    assert contract.sandbox == "isolated"
    assert contract.risk is CapabilityRisk.HIGH
    assert "常规 Bash 自动允许" in contract.approval_summary
    assert "sandbox_provider" not in AgentDraftSpec.model_fields


def test_operator_contract_uses_the_shared_sandbox_risk_copy() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    operator = draft(AgentTemplate.OPERATOR)

    contract = compiler.effective_contract(operator)

    assert contract.risk is CapabilityRisk.HIGH
    assert "常规 Bash 自动允许" in contract.approval_summary
    assert "高风险、越界或不确定动作" in contract.approval_summary


def test_local_development_profile_is_explicitly_preview_only() -> None:
    catalog = default_capability_catalog()
    profile = next(
        item for item in catalog.execution_profiles if item.profile_id == "local-development"
    )
    current = draft()
    local_draft = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "execution_profile": profile.profile_id,
                    "mcp_servers": ("tavily-readonly",),
                }
            )
        }
    )

    validation = AgentDraftCompiler(catalog).validate(local_draft)

    assert validation.ready is True
    assert validation.production_eligible is False
    preview_issue = next(
        issue for issue in validation.issues if issue.code == "execution_profile_preview_only"
    )
    assert preview_issue.stage.value == "production"
    assert "isolated-default" in preview_issue.suggested_profile_ids
    serialized = validation.model_dump(mode="json", by_alias=True)
    assert serialized["productionEligible"] is False
    serialized_issue = next(
        issue for issue in serialized["issues"] if issue["code"] == "execution_profile_preview_only"
    )
    assert serialized_issue["stage"] == "production"
    assert "isolated-default" in serialized_issue["suggestedProfileIds"]
    assert profile.sandbox_provider == "local"
    assert profile.production_allowed is False
    assert profile.risk is CapabilityRisk.HIGH
    assert NetworkAccess.EXTERNAL in profile.network_access
    assert profile.allowed_mcp_references == ("tavily-readonly",)


def test_orchestrator_starts_without_implicit_subagents() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    current = draft(AgentTemplate.ORCHESTRATOR)

    validation = compiler.validate(current)

    assert validation.ready is True
    assert current.spec.subagents == ()
    assert "subagents: []" in validation.manifest_yaml
    assert "builtin: Task" not in validation.manifest_yaml


def test_disabled_catalog_resources_fail_closed() -> None:
    catalog = default_capability_catalog()
    disabled = catalog.model_copy(
        update={
            "model_routes": tuple(
                item.model_copy(update={"enabled": False})
                if item.route_id == "deepseek-v4-pro"
                else item
                for item in catalog.model_routes
            ),
            "policies": tuple(
                item.model_copy(update={"enabled": False})
                if item.policy_id == "production-standard"
                else item
                for item in catalog.policies
            ),
            "execution_profiles": tuple(
                item.model_copy(update={"enabled": False}) for item in catalog.execution_profiles
            ),
        }
    )

    validation = AgentDraftCompiler(disabled).validate(draft())

    assert validation.ready is False
    assert {issue.code for issue in validation.issues} >= {
        "model_route_disabled",
        "policy_disabled",
        "execution_profile_disabled",
    }


def test_model_and_execution_profile_capabilities_must_be_compatible() -> None:
    catalog = default_capability_catalog()
    incompatible = catalog.model_copy(
        update={
            "model_routes": tuple(
                item.model_copy(update={"capabilities": ("streaming",)})
                if item.route_id == "deepseek-v4-pro"
                else item
                for item in catalog.model_routes
            ),
            "execution_profiles": tuple(
                item.model_copy(update={"network_access": (NetworkAccess.NONE,)})
                for item in catalog.execution_profiles
            ),
        }
    )
    current = draft()
    with_mcp = current.model_copy(
        update={"spec": current.spec.model_copy(update={"mcp_servers": ("tavily-readonly",)})}
    )

    validation = AgentDraftCompiler(incompatible).validate(with_mcp)

    assert validation.ready is False
    assert {issue.code for issue in validation.issues} >= {
        "model_capability_missing",
        "execution_profile_network_incompatible",
    }


def test_image_generation_route_cannot_be_used_as_agent_chat_model() -> None:
    catalog = default_capability_catalog()
    image_route = catalog.model_routes[0].model_copy(
        update={
            "route_id": "image-primary",
            "label": "图像生成",
            "models": ("image-1",),
            "model_type": "image_generation",
            "api_format": "openai_images",
            "capabilities": ("image_generation",),
        }
    )
    with_image = catalog.model_copy(update={"model_routes": (*catalog.model_routes, image_route)})
    current = draft()
    image_draft = current.model_copy(
        update={
            "spec": current.spec.model_copy(
                update={
                    "model": current.spec.model.model_copy(
                        update={
                            "route_id": "image-primary",
                            "model": "image-1",
                        }
                    )
                }
            )
        }
    )

    validation = AgentDraftCompiler(with_image).validate(image_draft)

    assert "model_route_not_conversational" in {issue.code for issue in validation.issues}


def test_execution_profile_egress_allows_only_registered_mcp_associations() -> None:
    catalog = default_capability_catalog()
    restricted = catalog.model_copy(
        update={
            "execution_profiles": tuple(
                item.model_copy(update={"allowed_mcp_references": ()})
                for item in catalog.execution_profiles
            )
        }
    )
    current = draft()
    with_mcp = current.model_copy(
        update={"spec": current.spec.model_copy(update={"mcp_servers": ("tavily-readonly",)})}
    )

    validation = AgentDraftCompiler(restricted).validate(with_mcp)

    assert "execution_profile_egress_incompatible" in {issue.code for issue in validation.issues}
    issue = next(
        issue
        for issue in validation.issues
        if issue.code == "execution_profile_egress_incompatible"
    )
    assert issue.related_references == ("tavily-readonly",)
    assert "tavily-readonly" in issue.message


def test_execution_profile_reports_network_and_egress_mismatches_together() -> None:
    catalog = default_capability_catalog()
    restricted = catalog.model_copy(
        update={
            "execution_profiles": tuple(
                item.model_copy(
                    update={
                        "network_access": (NetworkAccess.NONE,),
                        "allowed_mcp_references": (),
                    }
                )
                if item.profile_id == "isolated-default"
                else item
                for item in catalog.execution_profiles
            )
        }
    )
    current = draft()
    with_mcp = current.model_copy(
        update={"spec": current.spec.model_copy(update={"mcp_servers": ("tavily-readonly",)})}
    )

    validation = AgentDraftCompiler(restricted).validate(with_mcp)

    issues = {issue.code: issue for issue in validation.issues}
    assert validation.ready is False
    assert {
        "execution_profile_network_incompatible",
        "execution_profile_egress_incompatible",
    }.issubset(issues)
    assert issues["execution_profile_network_incompatible"].related_references == (
        "tavily-readonly",
    )


def test_all_new_and_imported_templates_have_no_operational_limits() -> None:
    from harness.studio.models import DraftLimits

    compiler = AgentDraftCompiler(default_capability_catalog())
    for template in AgentTemplate:
        source = draft(template)
        legacy_limits = DraftLimits.model_validate({
            "maxBudgetUsd": 4, "maxModelTokens": 400_000,
            "maxSubagentUsageUnits": 300_000, "timeoutSeconds": 300,
        })
        assert legacy_limits.max_budget_usd is None
        assert legacy_limits.max_model_tokens is None
        assert legacy_limits.max_subagent_usage_units is None
        source = source.model_copy(
            update={"spec": source.spec.model_copy(update={"limits": legacy_limits})}
        )
        compiled = compiler.compile(source)
        limits = yaml.safe_load(compiled.manifest_yaml)["spec"]["limits"]
        assert "maxBudgetUsd" not in limits
        assert "maxModelTokens" not in limits
        assert "maxSubagentUsageUnits" not in limits
        assert limits["timeoutSeconds"] == 300
        imported = parse_agent_bundle(compiled.bundle)
        assert imported.spec.limits.max_budget_usd is None


def test_skill_references_resolve_into_bundle_with_platform_provenance() -> None:
    compiler = AgentDraftCompiler(default_capability_catalog())
    base = draft()
    spec = base.spec.model_copy(update={"skill_references": ("office-docx", "office-xlsx")})
    candidate = base.model_copy(update={"spec": spec})

    resolved = compiler.resolve_skills(candidate)
    assert [skill.name for skill in resolved] == ["office-docx", "office-xlsx"]
    for skill in resolved:
        assert skill.source is not None
        assert skill.source.package_id == skill.name
        assert skill.source.modified is False
        assert len(skill.source.content_hash) == 64

    validation = compiler.validate(candidate)
    assert validation.ready
    assert "skills/office-docx" in yaml.safe_load(validation.manifest_yaml)["spec"]["skills"]

    compiled = compiler.compile(candidate)
    with ZipFile(BytesIO(compiled.bundle)) as bundle:
        packaged = set(bundle.namelist())
    assert "skills/office-docx/SKILL.md" in packaged
    assert "skills/office-xlsx/SKILL.md" in packaged


def test_skill_reference_conflicts_disable_and_unknown_block_compilation() -> None:
    compiler_catalog = default_capability_catalog()
    disabled = compiler_catalog.skills[0]
    catalog = compiler_catalog.model_copy(
        update={"skills": (disabled.model_copy(update={"enabled": False}), *compiler_catalog.skills[1:])}
    )
    compiler = AgentDraftCompiler(catalog)
    base = draft()
    spec = base.spec.model_copy(
        update={"skill_references": (disabled.package_id, "no-such-skill")}
    )
    candidate = base.model_copy(update={"spec": spec})

    validation = compiler.validate(candidate)
    codes = {issue.code for issue in validation.issues}
    assert {"skill_reference_disabled", "skill_reference_unknown"} <= codes
    assert not validation.ready
    assert compiler.resolve_skills(candidate) == ()
