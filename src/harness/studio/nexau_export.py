"""Export an editable Studio Draft as a portable, runnable NexAU Agent archive."""

from __future__ import annotations

import base64
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import yaml

from harness.studio.models import AgentDraft, DraftPythonTool, McpCapability


@dataclass(frozen=True)
class NexauAgentArchive:
    content: bytes
    filename: str


@dataclass(frozen=True)
class _NexauBuiltin:
    name: str
    binding: str
    description: str
    input_schema: dict[str, object]


_PATH_PROPERTY = {"type": "string", "description": "Workspace-relative path."}
_NEXAU_BUILTINS = {
    "Read": _NexauBuiltin(
        "read_file",
        "nexau.archs.tool.builtin.file_tools:read_file",
        "Read a text file from the current workspace, with optional line pagination.",
        {
            "type": "object",
            "properties": {
                "file_path": _PATH_PROPERTY,
                "offset": {"type": "number", "minimum": 0},
                "limit": {"type": "number", "minimum": 1},
            },
            "required": ["file_path"],
            "additionalProperties": False,
        },
    ),
    "Write": _NexauBuiltin(
        "write_file",
        "nexau.archs.tool.builtin.file_tools:write_file",
        "Write complete UTF-8 content to a file inside the current workspace.",
        {
            "type": "object",
            "properties": {
                "file_path": _PATH_PROPERTY,
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
            "additionalProperties": False,
        },
    ),
    "Glob": _NexauBuiltin(
        "list_directory",
        "nexau.archs.tool.builtin.file_tools:list_directory",
        "List files and subdirectories in a workspace directory.",
        {
            "type": "object",
            "properties": {
                "dir_path": _PATH_PROPERTY,
                "ignore": {"type": "array", "items": {"type": "string"}},
                "show_hidden": {"type": "boolean"},
            },
            "required": ["dir_path"],
            "additionalProperties": False,
        },
    ),
    "Grep": _NexauBuiltin(
        "search_file_content",
        "nexau.archs.tool.builtin.file_tools:search_file_content",
        "Search workspace file contents using a regular expression.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "dir_path": _PATH_PROPERTY,
                "include": {"type": "string"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    ),
    "Edit": _NexauBuiltin(
        "replace",
        "nexau.archs.tool.builtin.file_tools:replace",
        "Replace an exact text fragment inside a workspace file.",
        {
            "type": "object",
            "properties": {
                "file_path": _PATH_PROPERTY,
                "instruction": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "expected_replacements": {"type": "number", "minimum": 1},
            },
            "required": ["file_path", "instruction", "old_string", "new_string"],
            "additionalProperties": False,
        },
    ),
    "Bash": _NexauBuiltin(
        "run_shell_command",
        "nexau.archs.tool.builtin.shell_tools:run_shell_command",
        "Run a bounded shell command inside the configured NexAU sandbox.",
        {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "description": {"type": "string"},
                "is_background": {"type": "boolean"},
                "dir_path": _PATH_PROPERTY,
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    ),
}
_NEXAU_CONTEXT_TOKENS = 128_000
_NEXAU_OUTPUT_TOKENS = 16_000


def _skill_markdown(name: str, description: str, instructions: str) -> str:
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{instructions.strip()}\n"


def _python_module(tool: DraftPythonTool) -> str:
    return (
        f"{tool.code.rstrip()}\n\n"
        "_NEXAU_STUDIO_RUN = run\n\n"
        f"def {tool.name}(**arguments):\n"
        "    return _NEXAU_STUDIO_RUN(arguments)\n"
    )


def _write(archive: ZipFile, path: str, content: str | bytes) -> None:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def _tool_entry(builtin: _NexauBuiltin, *, prefix: str = "./") -> dict[str, object]:
    return {
        "name": builtin.name,
        "yaml_path": f"{prefix}tools/{builtin.name}.tool.yaml",
        "binding": builtin.binding,
    }


def _llm_config(model: str) -> dict[str, object]:
    return {
        "model": "${env.LLM_MODEL}",
        "base_url": "${env.LLM_BASE_URL}",
        "api_key": "${env.LLM_API_KEY}",
        "max_tokens": _NEXAU_OUTPUT_TOKENS,
        "temperature": 0.2,
        "stream": True,
        "api_type": (
            "anthropic_chat_completion"
            if model.lower().startswith("claude-")
            else "openai_chat_completion"
        ),
    }


def _env_name(reference: str, suffix: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", reference.upper()).strip("_")
    return f"NEXAU_MCP_{normalized}_{suffix}"


def _mcp_config(
    reference: str,
    capabilities: Mapping[str, McpCapability],
) -> tuple[dict[str, object], tuple[str, ...]]:
    capability = capabilities.get(reference)
    url_env = _env_name(reference, "URL")
    required_environment: list[str] = []
    if capability is None or capability.endpoint_url is None:
        url = f"${{env.{url_env}}}"
        required_environment.append(url_env)
    else:
        url = capability.endpoint_url

    server: dict[str, object] = {
        "name": capability.server_name if capability and capability.server_name else reference,
        "source_id": reference,
        "type": capability.transport if capability else "http",
        "url": url,
        "timeout": 30,
    }
    if capability is not None and capability.custom_headers:
        server["headers"] = dict(capability.custom_headers)
    if capability is None or capability.auth_mode == "none":
        return server, tuple(required_environment)

    credential_env = capability.credential_reference or _env_name(reference, "CREDENTIAL")
    required_environment.append(credential_env)
    placeholder = f"${{env.{credential_env}}}"
    if capability.auth_mode == "query" and capability.auth_name:
        server["url"] = f"{url}?{quote(capability.auth_name, safe='')}={placeholder}"
    elif capability.auth_mode == "bearer":
        server["headers"] = {
            **dict(capability.custom_headers),
            "Authorization": f"Bearer {placeholder}",
        }
    elif capability.auth_mode == "header" and capability.auth_name:
        server["headers"] = {
            **dict(capability.custom_headers),
            capability.auth_name: placeholder,
        }
    return server, tuple(required_environment)


def _subagent_prompt(alias: str, responsibility: str) -> str:
    return f"""# {alias}

你是主 Agent 的只读专长助手。

职责：{responsibility.strip()}

- 只分析当前工作区已经存在的材料，不调用公网、MCP 或其他 Sub Agent。
- 返回可核验的事实、文件路径、时间线、矛盾点和证据缺口。
- 明确区分事实、归因观点、分析推断和未决不确定性。
- 不编造比例、传播量、来源或已经完成的动作。
- 结果交回主 Agent；主 Agent 负责交叉核验、风险判断和最终答复。

skills 根目录位于 /home/user/.skills/。需要 Skill 时先通过运行时加载，不要从只读制品目录猜测路径。
"""


def _system_prompt(prompt: str) -> str:
    runtime_guidance = (
        "skills 根目录位于 /home/user/.skills/。需要 Skill 时先通过运行时加载；"
        "制品根目录只读，中间文件和最终产物写入 /home/user。"
    )
    content = prompt.rstrip()
    if "skills 根目录位于 /home/user/.skills/" not in content:
        content = f"{content}\n\n## NexAU 运行时路径\n\n{runtime_guidance}"
    return content + "\n"


def _deployment_guide(required_environment: set[str], mcp_references: tuple[str, ...]) -> str:
    variables = ["LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", *sorted(required_environment)]
    lines = "\n".join(f"- `{item}`" for item in variables)
    mcps = "、".join(f"`{item}`" for item in mcp_references) or "无"
    return f"""# NAC / NexAU 部署说明

此包由 Agent Studio 导出，凭据未写入 ZIP。导入 NAC 后，在项目环境变量中配置：

{lines}

模型名称、地址和凭据均由 NAC 项目环境注入。
原 Agent 使用的 MCP：{mcps}。MCP URL、Header 或 Query 凭据均通过环境变量解析。
发布前应在 NAC 预览环境完成模型、MCP、Sub Agent、文件工具和报告产物冒烟测试。
"""


def export_nexau_agent(
    draft: AgentDraft,
    *,
    mcp_capabilities: Mapping[str, McpCapability] | None = None,
) -> NexauAgentArchive:
    spec = draft.spec
    capabilities = mcp_capabilities or {}
    tools: list[dict[str, object]] = []
    mapped_builtins: list[_NexauBuiltin] = []
    for builtin_name in spec.builtin_tools:
        builtin = _NEXAU_BUILTINS.get(builtin_name)
        if builtin is not None:
            mapped_builtins.append(builtin)
            tools.append(_tool_entry(builtin))
    for tool in spec.python_tools:
        tools.append(
            {
                "name": tool.name,
                "yaml_path": f"./tools/{tool.name}.tool.yaml",
                "binding": f"custom_tools.{tool.name}:{tool.name}",
            }
        )

    required_environment: set[str] = set()
    mcp_servers: list[dict[str, object]] = []
    for reference in spec.mcp_servers:
        server, environment = _mcp_config(reference, capabilities)
        mcp_servers.append(server)
        required_environment.update(environment)

    sub_agents = [
        {
            "name": item.alias,
            "config_path": f"./subagents/{item.alias}/agent.yaml",
            "source_id": item.ref,
        }
        for item in spec.subagents
    ]
    unmapped = [
        name
        for name in spec.builtin_tools
        if name not in _NEXAU_BUILTINS and not (name == "Task" and spec.subagents)
    ]
    extensions = {
        "source": "Agent Studio",
        "version": spec.version,
        "route_id": spec.model.route_id,
        "permission_policy": spec.permission_policy,
        "execution_profile": spec.execution_profile,
        "workspace": spec.workspace.model_dump(mode="json", by_alias=True),
        "limits": spec.limits.model_dump(mode="json", by_alias=True),
        "evaluation_enabled": spec.evaluation_enabled,
        "evaluation_case_count": len(spec.evaluation_cases),
        "unmapped_builtin_tools": unmapped,
        "mcp_servers": list(spec.mcp_servers),
        "knowledge_references": list(spec.knowledge_references),
        "subagents": [
            {
                "alias": item.alias,
                "ref": item.ref,
                "description": item.responsibility,
                "background": item.background,
            }
            for item in spec.subagents
        ],
    }

    config: dict[str, object] = {
        "type": "agent",
        "name": spec.name,
        "description": spec.description,
        "system_prompt": "./systemprompt.md",
        "system_prompt_type": "jinja",
        "system_prompt_suffix": (
            "NexAU 运行时会压缩较早的工具结果；长任务应把来源、URL、时间、"
            "查询参数和不确定性持续写入工作区证据台账。Sub Agent 返回仅作为"
            "辅助证据，最终结论必须由主 Agent 交叉核验。"
        ),
        "tool_call_mode": "structured",
        "max_context_tokens": _NEXAU_CONTEXT_TOKENS,
        "max_iterations": spec.limits.max_turns or 100,
        "max_running_subagents": spec.limits.max_concurrent_subagents,
        "timeout": spec.limits.timeout_seconds or 300,
        "llm_config": _llm_config(spec.model.model),
        "tools": tools,
        "skills": [f"./skills/{skill.name}" for skill in spec.skills],
        "mcp_servers": mcp_servers,
        "sub_agents": sub_agents,
        "middlewares": [
            {
                "import": (
                    "nexau.archs.main_sub.execution.middleware.context_compaction:"
                    "ContextCompactionMiddleware"
                ),
                "params": {
                    "max_context_tokens": _NEXAU_CONTEXT_TOKENS,
                    "auto_compact": True,
                    "threshold": 0.75,
                    "compaction_strategy": "tool_result_compaction",
                    "keep_iterations": 4,
                    "emergency_compact_enabled": True,
                },
            }
        ],
    }

    manifest = {
        "agents": {spec.name: "agent.yaml"},
        "excluded": [".nexau/", ".env", "__pycache__/"],
    }

    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        _write(
            archive,
            "nexau.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        _write(
            archive,
            "agent.yaml",
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        )
        _write(
            archive,
            "agent-studio.json",
            json.dumps(extensions, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        _write(archive, "systemprompt.md", _system_prompt(spec.system_prompt))
        _write(
            archive,
            "NAC-DEPLOYMENT.md",
            _deployment_guide(required_environment, spec.mcp_servers),
        )
        for builtin in {item.name: item for item in mapped_builtins}.values():
            _write(
                archive,
                f"tools/{builtin.name}.tool.yaml",
                yaml.safe_dump(
                    {
                        "type": "tool",
                        "name": builtin.name,
                        "description": builtin.description,
                        "input_schema": builtin.input_schema,
                    },
                    sort_keys=False,
                    allow_unicode=True,
                ),
            )
        for skill in spec.skills:
            root = f"skills/{skill.name}"
            _write(
                archive,
                f"{root}/SKILL.md",
                _skill_markdown(skill.name, skill.description, skill.instructions),
            )
            for file in skill.files:
                payload = (
                    file.content.encode("utf-8")
                    if file.content is not None
                    else base64.b64decode(file.content_base64 or "", validate=True)
                )
                _write(archive, f"{root}/{file.path}", payload)
        for tool in spec.python_tools:
            _write(
                archive,
                f"tools/{tool.name}.tool.yaml",
                yaml.safe_dump(
                    {
                        "type": "tool",
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    },
                    sort_keys=False,
                    allow_unicode=True,
                ),
            )
            _write(archive, f"custom_tools/{tool.name}.py", _python_module(tool))
        read_only_builtins = [
            _NEXAU_BUILTINS[name] for name in ("Read", "Glob", "Grep") if name in spec.builtin_tools
        ]
        for subagent in spec.subagents:
            sub_root = f"subagents/{subagent.alias}"
            sub_config = {
                "type": "agent",
                "name": subagent.alias,
                "description": subagent.responsibility,
                "system_prompt": "./systemprompt.md",
                "system_prompt_type": "jinja",
                "tool_call_mode": "structured",
                "max_context_tokens": _NEXAU_CONTEXT_TOKENS,
                "max_iterations": min(spec.limits.max_turns or 24, 24),
                "timeout": spec.limits.timeout_seconds or 300,
                "llm_config": _llm_config(spec.model.model),
                "tools": [_tool_entry(builtin, prefix="../../") for builtin in read_only_builtins],
                "skills": [],
                "mcp_servers": [],
                "sub_agents": [],
                "middlewares": config["middlewares"],
            }
            _write(
                archive,
                f"{sub_root}/agent.yaml",
                yaml.safe_dump(sub_config, sort_keys=False, allow_unicode=True),
            )
            _write(
                archive,
                f"{sub_root}/systemprompt.md",
                _subagent_prompt(subagent.alias, subagent.responsibility),
            )

    return NexauAgentArchive(
        content=output.getvalue(),
        filename=f"{spec.name}-{spec.version}-nexau.zip",
    )
