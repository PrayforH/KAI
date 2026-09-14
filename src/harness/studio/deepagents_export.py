"""Export an editable Studio Draft as a runnable DeepAgents project.

The generated archive targets ``deepagents==0.7.13`` (PyPI, 2026-09-02,
requires-python ``>=3.11,<4.0``) and is a plain Python project: ``pip install
-e .`` plus model credentials are enough to run it. This is an export path,
not a runtime: nothing generated ever executes on this platform, so the
runtime-registry, capability-catalog and state-machine contracts stay
untouched.

Semantics that cannot survive the export are declared, never silently
dropped: knowledge bases, the memory bank, persisted approvals, Bash policy
gates and evals have no DeepAgents counterpart and are listed in
``agent-studio.json`` and the generated README.

Recipes verified against a real 0.7.13 install:

- A custom ``FilesystemMiddleware(tools=[...], backend=...)`` passed via
  ``middleware=[...]`` *replaces* the built-in one — this is how ``delete``
  is withheld and how ``execute`` is added only when the draft selects Bash.
- ``FilesystemPermission`` and execution-capable backends are mutually
  exclusive in 0.7.13 (``NotImplementedError``), so the Bash branch trades
  workspace permissions for ``execute``.
- ``write_todos`` is NOT in the default 0.7.13 middleware stack; it must be
  added explicitly via ``TodoListMiddleware()``.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import yaml
from packaging.version import InvalidVersion, Version

from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentVersion
from harness.studio.factory import create_draft_spec
from harness.studio.models import (
    AgentDraft,
    AgentDraftSpec,
    DraftLimits,
    DraftModelSelection,
    DraftPythonTool,
    DraftSkill,
    DraftSkillFile,
    DraftWorkspace,
    McpCapability,
    ModelRouteCapability,
    StudioModel,
)

DEEPAGENTS_PINNED_VERSION = "0.7.13"

_BUILTIN_TO_FS_TOOL = {
    "Read": "read_file",
    "Glob": "glob",
    "Grep": "grep",
    "Write": "write_file",
    "Edit": "edit_file",
    "Bash": "execute",
}
# Always exposed so the skills progressive-disclosure loop (ls -> read_file)
# stays usable; declared in agent-studio.json as export additions because the
# platform capability catalog has no matching entries.
_ALWAYS_ON_FS_TOOLS = ("ls", "read_file")

_ENV_PLACEHOLDER_PREFIX = "$__ENV__"
_ENV_PLACEHOLDER_SUFFIX = "__"

_MODEL_PROVIDERS = (
    ("claude", "anthropic"),
    ("gemini", "google_genai"),
)

_GITIGNORE = """# Credentials never leave .env; keep it out of version control.
.env
.venv/
__pycache__/
*.egg-info/
# Runtime workspace state
workspace/
# langgraph dev server state
.langgraph_api/
# Editor project files
.idea/
.vscode/
.DS_Store
"""


@dataclass(frozen=True)
class DeepagentsProjectArchive:
    content: bytes
    filename: str


class ProjectSourceFile(StudioModel):
    path: str
    size: int
    content: str | None
    unavailable: str | None = None


class DeepagentsProjectSource(StudioModel):
    revision: int
    filename: str
    digest: str
    framework_version: str
    files: tuple[ProjectSourceFile, ...]


def project_source(archive: DeepagentsProjectArchive, revision: int) -> DeepagentsProjectSource:
    """Read the exact export as bounded text previews; binary assets stay in the ZIP."""
    files: list[ProjectSourceFile] = []
    remaining = 2 * 1024 * 1024
    with ZipFile(io.BytesIO(archive.content)) as zipped:
        for entry in zipped.infolist():
            if entry.is_dir():
                continue
            content = None
            unavailable = None
            if entry.file_size > min(256 * 1024, remaining):
                unavailable = "文件超出预览大小限制，请下载项目查看。"
            else:
                raw = zipped.read(entry)
                try:
                    content = raw.decode("utf-8")
                    if "\x00" in content:
                        raise UnicodeError
                    remaining -= len(raw)
                except UnicodeError:
                    content = None
                    unavailable = "二进制文件，请下载项目查看。"
            files.append(ProjectSourceFile(
                path=entry.filename, size=entry.file_size, content=content,
                unavailable=unavailable,
            ))
    return DeepagentsProjectSource(
        revision=revision, filename=archive.filename,
        digest=hashlib.sha256(archive.content).hexdigest(),
        framework_version=DEEPAGENTS_PINNED_VERSION, files=tuple(files),
    )


@dataclass(frozen=True)
class _McpServerPlan:
    reference: str
    connection: dict[str, object]
    required_environment: tuple[str, ...]
    allowed_tools: dict[str, str] | None


def _python_identifier(name: str) -> str:
    sanitized = re.sub(r"\W", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    if keyword.iskeyword(sanitized):
        sanitized += "_"
    return sanitized or "field"


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in _python_identifier(name).split("_"))


_API_FORMAT_PROVIDER = {
    "anthropic_compatible": "anthropic",
    "openai_compatible": "openai",
}
_PROVIDER_CREDENTIAL_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
    "google_genai": ("GOOGLE_API_KEY", None),
}


def _deepagents_model(model: str, api_format: str | None = None) -> tuple[str, str]:
    """Resolve the DeepAgents ``provider:model`` string and its provider name.

    The platform route's ``apiFormat`` is authoritative when present; the model
    name heuristic is only a fallback, because platform route models are often
    aliases (``deepseek-v4-pro``) that say nothing about the wire protocol.
    """

    provider = _API_FORMAT_PROVIDER.get(api_format or "")
    if provider is None:
        lowered = model.lower()
        provider = next(
            (name for prefix, name in _MODEL_PROVIDERS if lowered.startswith(prefix)),
            "openai",
        )
    return f"{provider}:{model}", provider


def _env_placeholder(name: str) -> str:
    return f"{_ENV_PLACEHOLDER_PREFIX}{name}{_ENV_PLACEHOLDER_SUFFIX}"


def _env_name(reference: str, suffix: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", reference.upper()).strip("_")
    return f"DEEPAGENTS_MCP_{normalized}_{suffix}"


def _write(archive: ZipFile, path: str, content: str | bytes) -> None:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    info = ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def _skill_markdown(name: str, description: str, instructions: str) -> str:
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{instructions.strip()}\n"


def _mcp_server_plan(
    reference: str,
    capabilities: Mapping[str, McpCapability],
) -> _McpServerPlan:
    """Resolve one catalog MCP reference into a langchain-mcp-adapters entry."""

    capability = capabilities.get(reference)
    url_env = _env_name(reference, "URL")
    required_environment: list[str] = []
    if capability is None or capability.endpoint_url is None:
        url = _env_placeholder(url_env)
        required_environment.append(url_env)
    else:
        url = capability.endpoint_url

    headers: dict[str, str] = dict(capability.custom_headers) if capability else {}
    transport = (capability.transport if capability else "http").lower()
    credential_env: str | None = None
    if capability is not None and capability.auth_mode not in (None, "none"):
        credential_env = capability.credential_reference or _env_name(reference, "CREDENTIAL")
        required_environment.append(credential_env)
        placeholder = _env_placeholder(credential_env)
        if capability.auth_mode == "query" and capability.auth_name:
            url = f"{url}?{quote(capability.auth_name, safe='')}={placeholder}"
        elif capability.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {placeholder}"
        elif capability.auth_mode == "header" and capability.auth_name:
            headers[capability.auth_name] = placeholder

    # langchain-mcp-adapters 0.3.x connection kinds: streamable_http is the
    # default for ``http`` transports; sse and websocket are named kinds.
    transport_kind = {
        "http": "streamable_http",
        "streamable_http": "streamable_http",
        "streamable-http": "streamable_http",
        "sse": "sse",
        "websocket": "websocket",
    }.get(transport, "streamable_http")

    connection: dict[str, object] = {"url": url, "transport": transport_kind, "timeout": 30.0}
    if headers:
        connection["headers"] = headers
    return _McpServerPlan(
        reference=reference,
        connection=connection,
        required_environment=tuple(required_environment),
        allowed_tools={name.split("__", 2)[-1]: name for name in capability.tools}
        if capability else None,
    )


_MCP_MODULE = '''"""MCP servers resolved from the Agent Studio catalog at export time.

Credential values are never exported. Every ``$__ENV__NAME__`` placeholder is
resolved from the environment at startup and fails fast when unset.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote

from langchain.agents.middleware import AgentMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

_PLACEHOLDER = re.compile(r"\\$__ENV__(\\w+)__")


def _resolve_placeholders(value, *, url=False):
    if isinstance(value, str):
        def _replace(match):
            name = match.group(1)
            resolved = os.environ.get(name)
            if not resolved:
                raise RuntimeError(f"Missing environment variable {name}; see .env.example")
            return quote(resolved, safe="") if url and "?" in value[:match.start()] else resolved

        return _PLACEHOLDER.sub(_replace, value)
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resolve_placeholders(item) for item in value]
    return value


SERVER_CONNECTIONS = /*CONNECTIONS*/
ALLOWED_TOOLS = /*ALLOWED_TOOLS*/

_CACHED_TOOLS: list | None = None


def resolved_connections() -> dict:
    return {server: {key: _resolve_placeholders(value, url=key == "url")
                     for key, value in connection.items()}
            for server, connection in SERVER_CONNECTIONS.items()}


async def load_mcp_tools() -> list:
    """Discover remote tools once per process."""

    global _CACHED_TOOLS
    if _CACHED_TOOLS is None:
        connections = resolved_connections()
        client = MultiServerMCPClient(connections)
        discovered = []
        for server in connections:
            allowed = ALLOWED_TOOLS[server]
            remote = await client.get_tools(server_name=server)
            selected = [tool for tool in remote if allowed is None or tool.name in allowed]
            if allowed is not None and set(allowed) - {tool.name for tool in selected}:
                raise RuntimeError(f"MCP server {server} is missing catalog tools")
            # Preserve Studio names and avoid collisions across MCP servers.
            for tool in selected:
                name = (allowed[tool.name] if allowed is not None
                        else f"mcp__{server}__{tool.name}")
                discovered.append(tool.model_copy(update={"name": name}))
        _CACHED_TOOLS = discovered
    return _CACHED_TOOLS


class McpToolsMiddleware(AgentMiddleware):
    """Attach MCP tools on the first model call.

    Tool discovery is a remote round-trip, so it cannot run inside the graph
    factory: ``langgraph.json`` entry points must be synchronous callables
    (langgraph_api invokes factories without awaiting). Loading here keeps the
    factory sync while the tools still reach the model.
    """

    async def awrap_model_call(self, request, handler):
        tools = await load_mcp_tools()
        if not tools:
            return await handler(request)
        return await handler(request.override(tools=[*request.tools, *tools]))

    async def awrap_tool_call(self, request, handler):
        tool = next((item for item in await load_mcp_tools()
                     if item.name == request.tool_call["name"]), None)
        return await handler(request.override(tool=tool) if tool else request)

    def wrap_model_call(self, request, handler):
        raise RuntimeError(
            "MCP tools are loaded asynchronously; invoke this graph with "
            "ainvoke()/astream() instead of the sync path."
        )
'''


def _render_mcp_module(plans: tuple[_McpServerPlan, ...]) -> str:
    connections = {plan.reference: plan.connection for plan in plans}
    # Token replacement, not str.format: the module body is full of braces
    # that .format would try to interpret as fields.
    return (_MCP_MODULE.replace("/*CONNECTIONS*/", repr(connections))
            .replace("/*ALLOWED_TOOLS*/", repr({p.reference: p.allowed_tools for p in plans})))


_TOOL_MODULE = '''"""{title}: Studio Python tool exported as a DeepAgents StructuredTool.

The original operator keeps the platform contract: ``run(arguments) -> dict``.
The tool schema exposed to the model equals the Studio ``inputSchema``.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from langchain_core.tools import StructuredTool
from jsonschema import validate

INPUT_SCHEMA = json.loads({schema!r})

from .operators.{title} import run


async def _invoke(**arguments: Any) -> dict:
    # The Studio operator may be sync (``def run``) or async
    # (``async def run``); both satisfy the platform contract.
    validate(arguments, INPUT_SCHEMA)
    outcome = run(arguments)
    if inspect.isawaitable(outcome):
        outcome = await outcome
    return outcome


def build() -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=_invoke,
        name={name!r},
        description={description!r},
        args_schema=INPUT_SCHEMA,
    )
'''


def _render_tool_module(tool: DraftPythonTool) -> str:
    return _TOOL_MODULE.format(
        title=tool.name,
        schema=json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True, indent=4),
        pascal=_pascal(tool.name),
        name=tool.name,
        description=tool.description,
    )


def _render_agent_py(
    *,
    spec: AgentDraftSpec,
    model: str,
    fs_tools: tuple[str, ...],
    has_bash: bool,
    shell_timeout: int,
    read_only: bool,
    permissions: bool,
    with_mcp: bool,
    with_skills: bool,
    recursion_limit: int,
    module_prefix: str,
) -> str:
    tool_imports = "\n".join(
        f"from {module_prefix}tools.{tool.name} import build as build_{tool.name}"
        for tool in spec.python_tools
    )
    tool_builds = "\n".join(f"        build_{tool.name}()," for tool in spec.python_tools)
    subagent_imports = "\n".join(
        f"from {module_prefix}subagents.{_python_identifier(item.alias)}.agent "
        f"import build_agent as build_{_python_identifier(item.alias)}"
        for item in spec.subagents
    )
    subagent_entries = "\n".join(
        f"        {{'name': {item.alias!r}, 'description': {item.responsibility!r}, "
        f"'runnable': build_{_python_identifier(item.alias)}(model=model)}},"
        for item in spec.subagents
    )

    if has_bash:
        backend_import = "from deepagents.backends import LocalShellBackend"
        backend_literal = (
            "LocalShellBackend(root_dir=WORKSPACE_ROOT, "
            f"timeout={shell_timeout}, virtual_mode=True)"
        )
    else:
        backend_import = "from deepagents.backends import FilesystemBackend"
        backend_literal = "FilesystemBackend(root_dir=WORKSPACE_ROOT)"

    permission_import = (
        "from deepagents import FilesystemPermission\n" if permissions else ""
    )
    permissions_block = (
        'PERMISSIONS = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]\n'
        if permissions
        else ""
    )
    permissions_argument = "permissions=PERMISSIONS," if permissions else ""
    # Named mcp_servers (not mcp): a top-level mcp module would shadow the
    # third-party mcp SDK that langchain-mcp-adapters imports.
    mcp_import = f"from {module_prefix}mcp_servers import McpToolsMiddleware\n" if with_mcp else ""
    mcp_middleware_entry = "    McpToolsMiddleware(),\n" if with_mcp else ""
    # Read-only drafts that keep Bash get an approval gate on execute.
    with_interrupts = has_bash and read_only
    interrupt_block = ('interrupt_on={"execute": True, "write_file": True, "edit_file": True},'
                       if with_interrupts else "")
    skills_argument = 'skills=["skills/"],' if with_skills else ""

    build_body = f'''def build_agent(model=None):
    """Build the compiled deep agent. Pass a BaseChatModel to override MODEL."""

    subagents = [
{subagent_entries}
    ]
    delegation = (SubAgentMiddleware(backend=BACKEND, subagents=subagents)
                  if subagents else NoSubagentsMiddleware())
    return create_deep_agent(
        model=model or MODEL,
        tools=[
{tool_builds}        ],
        system_prompt=SYSTEM_PROMPT,
        middleware=[*MIDDLEWARE, delegation],
        {skills_argument}
        {permissions_argument}
        backend=BACKEND,
        {interrupt_block}
        name=AGENT_NAME,
    ).with_config(recursion_limit=RECURSION_LIMIT)


def agent():
    """LangGraph entry point (``langgraph.json`` -> ``./agent.py:agent``).

    Must stay a synchronous zero-argument callable: langgraph_api invokes graph
    factories without awaiting them, and it is called lazily so importing this
    module never blocks the event loop.
    """

    return build_agent()
'''

    model_env = ("DEEPAGENTS_MODEL_" + module_prefix.replace(".", "_").upper().strip("_")
                 if module_prefix else "DEEPAGENTS_MODEL")
    return f'''"""DeepAgents assembly for {spec.name} {spec.version}.

Generated by Agent Studio; runtime pinned to deepagents=={DEEPAGENTS_PINNED_VERSION}.
``build_agent(model=...)`` accepts a BaseChatModel instance for testing, and
``agent()`` is the LangGraph entry point declared in ``langgraph.json``.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from deepagents import FilesystemMiddleware, SubAgentMiddleware, create_deep_agent
{permission_import}{backend_import}
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
{mcp_import}
{tool_imports}
{subagent_imports}

# .env lives next to agent.py; real environment variables keep precedence and
# a missing file is a silent no-op.
load_dotenv(Path(__file__).resolve().parent / ".env")

AGENT_NAME = {spec.name!r}
MODEL = os.environ.get({model_env!r}, {model!r})
SYSTEM_PROMPT = {spec.system_prompt!r}
WORKSPACE_ROOT = Path(__file__).resolve().parent / "workspace"
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
RECURSION_LIMIT = {recursion_limit}

# No checkpointer is passed on purpose: LangGraph API/Platform owns persistence
# and raises on a graph that brings its own under `langgraph dev`.

{'''def _materialize_skills() -> None:
    """Mirror exported skills into the workspace (SkillsMiddleware reads it
    relative to the backend root, like the platform's .claude/skills)."""

    source = Path(__file__).resolve().parent / "skills"
    if not source.is_dir():
        return
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    target = WORKSPACE_ROOT / "skills"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


_materialize_skills()


''' if with_skills else ''}{permissions_block}
class NoSubagentsMiddleware(AgentMiddleware):
    # Replace the default delegation slot: no implicit general-purpose child.
    @property
    def name(self):
        return "SubAgentMiddleware"


BACKEND = {backend_literal}

# Replaces the built-in FilesystemMiddleware (verified on 0.7.13): `delete` is
# withheld; `execute` is present only because the draft selects Bash.
_FILESYSTEM_TOOLS = {list(fs_tools)!r}
MIDDLEWARE = [
    TodoListMiddleware(),
    FilesystemMiddleware(backend=BACKEND, tools=_FILESYSTEM_TOOLS,
                         _permissions={"PERMISSIONS" if permissions else "None"}),
{mcp_middleware_entry}]

{build_body}'''


def _render_langgraph_json(name: str) -> str:
    """LangGraph entry point: the graph id maps to ``./agent.py:agent``."""

    return json.dumps(
        {
            "dependencies": ["."],
            "graphs": {name: "./agent.py:agent"},
            "env": ".env",
        },
        indent=2,
    ) + "\n"


def _render_pyproject(name: str, version: str, *, with_mcp: bool) -> str:
    dependencies = [
        f'"deepagents=={DEEPAGENTS_PINNED_VERSION}"',
        # deepagents 0.7.13 ships langchain-anthropic/langchain-google-genai but
        # NOT langchain-openai, which the openai-compatible default needs.
        '"langchain-openai>=1.0,<2.0"',
        # .env loading promised by the README; load_dotenv runs in agent.py.
        '"python-dotenv>=1.0,<2.0"',
        '"jsonschema>=4.0,<5.0"',
        # `langgraph dev` (local server + Studio) is the project's entry point.
        '"langgraph-cli[inmem]>=0.4,<0.5"',
    ]
    if with_mcp:
        dependencies.append('"langchain-mcp-adapters>=0.3,<0.4"')
    joined = ",\n    ".join(dependencies)
    # The MCP connector module must NOT be named ``mcp``: that would shadow
    # the third-party ``mcp`` package imported by langchain-mcp-adapters.
    modules = ['"agent"'] + (['"mcp_servers"'] if with_mcp else [])
    return f'''[project]
name = {json.dumps(name)}
version = {json.dumps(version)}
description = "DeepAgents project exported from Agent Studio"
requires-python = ">=3.11,<4.0"
dependencies = [
    {joined},
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = [{", ".join(modules)}]

[tool.setuptools.packages.find]
include = ["tools*", "subagents*"]
'''


def _render_readme(
    *,
    spec: AgentDraftSpec,
    model: str,
    route_id: str,
    required_environment: tuple[str, ...],
    dropped: Mapping[str, str],
    added_tools: tuple[str, ...],
    has_bash: bool,
    with_mcp: bool,
) -> str:
    env_lines = "\n".join(f"- `{item}`" for item in required_environment) or "- （无 MCP 凭据）"
    dropped_lines = "\n".join(f"- **{key}**：{value}" for key, value in dropped.items())
    added_line = "、".join(f"`{item}`" for item in added_tools) or "无"
    if has_bash:
        bash_note = (
            "`execute` 在宿主机 shell 中运行，**没有**平台侧的 Bash 安全策略门"
            "（`bash_safety` 策略引擎不随导出生效），请在可信环境运行。"
        )
    else:
        bash_note = "草稿未选择 Bash，本项目没有 `execute` 工具。"
    mcp_note = (
        "MCP 连接在 `mcp_servers.py` 中声明，凭据通过环境变量注入；"
        "远端工具在首次模型调用时由 `McpToolsMiddleware` 拉取（图工厂是同步的，"
        "不能在里面做异步发现），因此**必须走异步路径**（`langgraph dev` / `ainvoke`）。"
        "项目启动时自动读取 `.env`（已存在的环境变量优先）。"
        if with_mcp
        else "本项目未选择 MCP 服务器。项目启动时自动读取 `.env`（已存在的环境变量优先）。"
    )
    return f'''# {spec.display_name} · DeepAgents 导出项目

由 Agent Studio 从草稿 `{spec.name}` `{spec.version}` 导出，
运行时钉死为 `deepagents=={DEEPAGENTS_PINNED_VERSION}`（Python >= 3.11）。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e .
cp .env.example .env          # 填入模型凭据
langgraph dev                 # 本地 LangGraph 服务 + Studio（浏览器自动打开）
```

`langgraph.json` 把图声明为 `./agent.py:agent`——这是本项目的入口。
`langgraph dev` 会起本地服务并提供 Studio 界面：可对话、看消息与工具调用、
处理审批中断。

> macOS 的 python.org 安装只有 `python3`/`pip3`（venv 激活后 `python`/`pip` 才存在）。
> 若 `pip install` 报 `CERTIFICATE_VERIFY_FAILED`，改用
> `SSL_CERT_FILE=/etc/ssl/cert.pem pip install -e .`。

## 在 IDE（PyCharm / VS Code）里运行

1. 把项目 venv 设为解释器（PyCharm 自动识别的 `.venv` 即可）。
2. **确认该 venv 里有 pip，并把本项目装进去**——`langgraph dev` 要求项目已安装，
   否则会报 "you haven't installed your project and its dependencies yet"：

   ```bash
   python -m ensurepip --upgrade     # PyCharm 新建的 venv 可能不带 pip
   python -m pip install -e .        # 装进当前解释器
   ```

3. 在 IDE 的终端里运行 `langgraph dev`（服务在 2024 端口，Studio 界面随之可用）。
   也可以直接在解释器里调试图：

   ```python
   from agent import agent
   graph = agent()
   ```

> 频繁踩 `CERTIFICATE_VERIFY_FAILED` 的话，用 [`uv`](https://docs.astral.sh/uv/)
> 更省事：`uv venv && uv pip install -e .`；企业证书环境可使用 `--system-certs`。

### 无界面调用

```python
from agent import agent
graph = agent()                       # 同步零参工厂，与 langgraph.json 同一入口
result = await graph.ainvoke(
    {{"messages": [{{"role": "user", "content": "任务"}}]}},
    config={{"configurable": {{"thread_id": "t1"}}}},
)
```

## 模型

`agent.py` 的 `MODEL` 默认 `{model}`，可用 `DEEPAGENTS_MODEL` 覆盖。

**平台的模型凭据不随导出提供**（平台路由 `{route_id}` 的凭据由平台托管），
你必须提供自己能访问的 provider 凭据与端点。按实际 provider 配置：

| provider | 必需变量 |
| --- | --- |
| Anthropic 协议端点 | `ANTHROPIC_API_KEY`（自建端点另加 `ANTHROPIC_BASE_URL`） |
| OpenAI 兼容端点 | `OPENAI_API_KEY`（自建端点另加 `OPENAI_BASE_URL`） |
| Google | `GOOGLE_API_KEY` |

这三组**互相独立，只配你实际使用的那一组**，不需要全填。换 provider 时同时改
`DEEPAGENTS_MODEL` 的前缀（如 `openai:deepseek-chat`）。

## 环境变量

- `DEEPAGENTS_MODEL`（可选，覆盖默认模型）
{env_lines}

## 工作区与执行

文件工具以 `./workspace/` 为根（首次运行自动创建）。{bash_note}
上下文压缩（SummarizationMiddleware）与 Anthropic prompt caching 由 0.7.13 默认栈提供。

## 自定义 Python 算子

`tools/operators/` 保留原始源码（包括 future imports），`tools/` 包装器保留完整
JSON Schema 并验证入参。算子额外使用的第三方库需要加入 `pyproject.toml`；
平台镜像中的预装包、系统命令和其他私有模块不会自动打包。

## 子智能体

固定版本子智能体位于 `subagents/<别名>/`，包含各自真实的提示词、工具、技能、
模型配置与导出边界说明。按每个目录内的 `.env.example` 配置凭据；子智能体支持
独立模型变量，默认使用自己的模型；同一 provider 的凭据使用进程级环境变量共享。
平台后台调度改为同步委派，父子各自目录中的 README 列出了未迁移的平台能力。

## 会话与审批

图**不自带 checkpointer**：LangGraph API/Platform 自己管理持久化，`langgraph dev`
遇到自带 checkpointer 的图会直接报错。因此会话状态由平台侧保存，`thread_id`
即会话标识，`interrupt_on` 的审批中断也由平台侧挂起与恢复——这正是本项目的运行方式。

（若要脱离 LangGraph 自己驱动图，需自行传入 checkpointer，否则
`Command(resume=...)` 会报 `Cannot use resume without checkpointer`。）

## 导出时追加的文件工具

{added_line}。技能渐进披露依赖 `ls` -> `read_file`，因此始终可用；
平台能力目录没有对应条目，已在 `agent-studio.json` 的 `addedExportTools` 声明。
`delete` 工具被显式裁剪。

## 未导出的平台语义

{dropped_lines}

{mcp_note}
'''


def _render_env_example(
    required_environment: tuple[str, ...],
    *,
    provider: str,
    model: str,
    route_id: str,
    api_format: str | None,
    credential_managed: bool,
) -> str:
    credential_env, base_url_env = _PROVIDER_CREDENTIAL_ENV.get(
        provider, ("OPENAI_API_KEY", "OPENAI_BASE_URL")
    )
    alternatives = [
        name for name, _ in _PROVIDER_CREDENTIAL_ENV.values() if name != credential_env
    ]
    lines = [
        "# 复制本文件为 .env 后填写；绝不提交真实凭据。项目启动时自动读取（已有环境变量优先）。",
        "#",
        f"# 本项目的模型来自平台路由 `{route_id}`（apiFormat={api_format or 'unknown'}），",
        f"# 默认模型串为 `{model}`。",
    ]
    if credential_managed:
        lines.append(
            "# 平台的模型凭据由平台托管，**不随导出提供**：你需要自备 provider 凭据与端点，"
            "或改用你自己能访问的模型。"
        )
    lines.extend(
        [
            "#",
            "# ---- 必需：你所用 provider 的凭据 ----",
            f"# {credential_env}=<你的 key>",
        ]
    )
    if base_url_env is not None:
        lines.append(
            f"# {base_url_env}=<你的端点；使用 provider 官方端点可留空>"
        )
    lines.extend(["#", "# ---- 可选：覆盖默认模型 ----"])
    if alternatives:
        lines.append(
            "# 若换用其他 provider，改 DEEPAGENTS_MODEL 并改为对应的 "
            + " / ".join(alternatives)
        )
    lines.append(f"# DEEPAGENTS_MODEL={model}")
    if required_environment:
        lines.extend(["#", "# ---- MCP 凭据（来自草稿选择的 MCP 服务器）----"])
        lines.extend(f"# {name}=<...>" for name in required_environment)
    return "\n".join(lines) + "\n"



def draft_from_published_snapshot(parent: AgentDraft, version: AgentVersion) -> AgentDraft:
    """Use immutable publication assets, never the possibly edited current draft."""
    snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
    source = snapshot.manifest.spec
    if source.subagents or source.hooks:
        raise ConflictError(f"子智能体包含无法导出的嵌套委派或 hooks：{version.name}")
    if snapshot.content_hash != version.manifest_hash:
        raise ConflictError(f"子智能体快照与发布版本不匹配：{version.name}")
    skills: list[DraftSkill] = []
    for skill in snapshot.skill_snapshots:
        markdown = next((file for file in skill.files if file.path == "SKILL.md"), None)
        if markdown is None:
            raise ConflictError(f"子智能体 Skill 缺少 SKILL.md：{skill.name}")
        text = base64.b64decode(markdown.content_base64, validate=True).decode("utf-8")
        instructions = text.split("---", 2)[-1].strip() if text.startswith("---") else text
        skills.append(DraftSkill(
            name=skill.name, description=skill.description, instructions=instructions,
            files=tuple(DraftSkillFile(path=file.path, contentBase64=file.content_base64)
                        for file in skill.files if file.path != "SKILL.md"),
        ))
    python_tools: list[DraftPythonTool] = []
    snapshots = {item.reference: item for item in snapshot.python_tool_snapshots}
    for tool in source.tools:
        if tool.python_entry is None:
            continue
        asset = snapshots.get(tool.python_entry)
        if asset is None:
            raise ConflictError(f"子智能体 Python 工具缺少源码：{tool.python_entry}")
        code = base64.b64decode(asset.content_base64, validate=True).decode("utf-8")
        python_tools.append(DraftPythonTool(
            name=asset.name, description=asset.description,
            inputSchema=asset.input_schema, code=code,
        ))
    spec = create_draft_spec(
        name=version.name, domain="exported-subagent", display_name=version.name,
        description="固定发布版本子智能体", template=parent.spec.template,
    ).model_copy(update={
        "version": version.version,
        "runtime": source.runtime,
        "system_prompt": snapshot.system_prompt,
        "model": DraftModelSelection(
            routeId=source.model.route, model=source.model.model,
            fallbackRouteId=source.model.fallback_route,
            fallbackModel=source.model.fallback_model,
            requiredCapabilities=source.model.required_capabilities,
        ),
        "skills": tuple(skills), "skill_references": (),
        "python_tools": tuple(python_tools),
        "builtin_tools": tuple(t.builtin for t in source.tools if t.builtin is not None),
        "mcp_servers": tuple(t.mcp for t in source.tools if t.mcp is not None),
        "knowledge_references": source.knowledge_references,
        "tool_exposure_mode": source.tool_exposure_mode,
        "subagents": (), "permission_policy": source.permissions.policy,
        "workspace": DraftWorkspace(
            restoreSession=source.workspace.restore_session,
            archiveOnComplete=source.workspace.archive_on_complete,
        ),
        "limits": DraftLimits.model_validate({
            key: value for key, value in source.limits.model_dump().items()
            if key in DraftLimits.model_fields
        }),
        "evaluation_enabled": False,
    })
    return parent.model_copy(update={"spec": spec})


def export_deepagents_project(
    draft: AgentDraft,
    *,
    skills: tuple[DraftSkill, ...] | None = None,
    mcp_capabilities: Mapping[str, McpCapability] | None = None,
    model_route: ModelRouteCapability | None = None,
    subagent_drafts: Mapping[str, AgentDraft] | None = None,
    model_routes: Mapping[str, ModelRouteCapability] | None = None,
    module_prefix: str = "",
) -> DeepagentsProjectArchive:
    """Render one Studio draft as a runnable DeepAgents 0.7.13 project zip."""

    spec = draft.spec
    try:
        Version(spec.version)
        for tool in spec.python_tools:
            ast.parse(tool.code)
    except (InvalidVersion, SyntaxError) as error:
        raise ConflictError(f"草稿版本号或 Python 工具不能生成可安装项目：{error}") from error
    packages = [_python_identifier(item.alias) for item in spec.subagents]
    if len(packages) != len(set(packages)):
        raise ConflictError("子智能体别名转换为 Python 模块名后重复")
    children = subagent_drafts or {}
    for binding in spec.subagents:
        child = children.get(binding.ref)
        if (child is None or child.spec.subagents
                or f"{child.spec.name}@{child.spec.version}" != binding.ref):
            raise ConflictError(f"无法完整导出固定版本子智能体：{binding.ref}")
    resolved_skills = tuple(skills) if skills is not None else tuple(spec.skills)
    capabilities = mcp_capabilities or {}

    has_bash = "Bash" in spec.builtin_tools
    fs_tools: list[str] = list(_ALWAYS_ON_FS_TOOLS)
    for builtin in spec.builtin_tools:
        mapped = _BUILTIN_TO_FS_TOOL.get(builtin)
        if mapped and mapped not in fs_tools:
            fs_tools.append(mapped)
    selected_fs = {
        mapped
        for builtin in spec.builtin_tools
        if (mapped := _BUILTIN_TO_FS_TOOL.get(builtin)) is not None
    }
    added_tools = tuple(tool for tool in _ALWAYS_ON_FS_TOOLS if tool not in selected_fs)

    # 0.7.13 constraint: FilesystemPermission requires a backend without
    # command execution, so permissions survive only on the no-Bash branch.
    shell_timeout = min(spec.limits.timeout_seconds or 300, 3600)
    read_only = spec.permission_policy == "production-read-only"
    permissions = read_only and not has_bash

    # The route catalog carries the authoritative wire protocol; the model name
    # alone (a platform alias such as `deepseek-v4-pro`) cannot decide it.
    api_format = model_route.api_format if model_route is not None else None
    credential_managed = (
        model_route.credential_managed if model_route is not None else False
    )
    model, provider = _deepagents_model(spec.model.model, api_format)
    mcp_plans = tuple(
        _mcp_server_plan(reference, capabilities) for reference in spec.mcp_servers
    )
    required_environment = sorted({
        name for plan in mcp_plans for name in plan.required_environment
    })

    dropped: dict[str, str] = {}
    if spec.knowledge_references:
        dropped["知识库"] = (
            f"草稿引用了知识库 {', '.join(spec.knowledge_references)}；"
            "DeepAgents 没有对应工具，导出项目不能检索知识库。"
        )
    dropped["平台记忆"] = (
        "MemoryBank（同意/保留/敏感级）不导出；不要启用 DeepAgents memory=[...]，"
        "那是绕过同意策略的文件式记忆。"
    )
    dropped["持久化审批"] = (
        "ApprovalService（WAITING_APPROVAL / TTL / CAS 决策）不可导出；"
        "导出项目使用 LangGraph 会话与 interrupt_on，不承接平台原有审批记录与策略。"
    )
    if has_bash:
        dropped["Bash 策略门"] = (
            "execute 使用宿主机权限，不经过 bash_safety，也不受 workspace 路径隔离。"
        )
    if spec.subagents:
        dropped["子智能体调度"] = (
            "已导出固定版本的提示词、工具、模型和技能；background 调度转为同步委派，"
            "子智能体使用自己的 workspace，不共享平台会话、配额和调度状态。"
        )
    unmapped = [name for name in spec.builtin_tools
                if name not in _BUILTIN_TO_FS_TOOL and name != "Task"]
    if unmapped:
        dropped["未映射工具"] = ", ".join(unmapped) + "；导出项目不提供这些平台工具。"
    dropped["运行限制"] = (
        "maxTurns 近似映射为 LangGraph recursion_limit；Bash 保留单次超时。"
        "其余预算、调用次数、总超时、并发限制以及模型回退策略不由导出项目强制执行。"
    )
    dropped["评测"] = "评测用例属于平台 evals，不随项目导出。"
    dropped["配额与遥测"] = (
        "usage 结算、上下文遥测、execution_profile 与模型路由（routeId）仅在平台内有效。"
    )

    extensions = {
        "source": "Agent Studio",
        "exporter": "deepagents_export",
        "deepagentsVersion": DEEPAGENTS_PINNED_VERSION,
        "version": spec.version,
        "routeId": spec.model.route_id,
        "model": spec.model.model,
        "deepagentsModel": model,
        "deepagentsProvider": provider,
        "routeApiFormat": api_format,
        "routeCredentialManaged": credential_managed,
        "permissionPolicy": spec.permission_policy,
        "executionProfile": spec.execution_profile,
        "workspace": spec.workspace.model_dump(mode="json", by_alias=True),
        "limits": spec.limits.model_dump(mode="json", by_alias=True),
        "builtinTools": list(spec.builtin_tools),
        "filesystemTools": fs_tools,
        "addedExportTools": list(added_tools),
        "unmappedBuiltinTools": [
            name
            for name in spec.builtin_tools
            if name not in _BUILTIN_TO_FS_TOOL and name != "Task"
        ],
        "skillReferences": list(spec.skill_references),
        "knowledgeReferences": list(spec.knowledge_references),
        "mcpServers": list(spec.mcp_servers),
        "subagents": [
            {
                "alias": item.alias,
                "ref": item.ref,
                "responsibility": item.responsibility,
            }
            for item in spec.subagents
        ],
        "droppedSemantics": dropped,
    }

    agent_py = _render_agent_py(
        spec=spec,
        model=model,
        fs_tools=tuple(fs_tools),
        has_bash=has_bash,
        shell_timeout=shell_timeout,
        read_only=read_only,
        permissions=permissions,
        with_mcp=bool(spec.mcp_servers),
        with_skills=bool(resolved_skills),
        recursion_limit=max((spec.limits.max_turns or 100) * 2, 50),
        module_prefix=module_prefix,
    )

    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        _write(
            archive,
            "pyproject.toml",
            _render_pyproject(spec.name, spec.version, with_mcp=bool(
                spec.mcp_servers or any(c.spec.mcp_servers for c in children.values()))),
        )
        readme = _render_readme(
            spec=spec,
            model=model,
            route_id=spec.model.route_id,
            required_environment=tuple(required_environment),
            dropped=dropped,
            added_tools=added_tools,
            has_bash=has_bash,
            with_mcp=bool(spec.mcp_servers),
        )
        env_example = _render_env_example(
            tuple(required_environment),
            provider=provider,
            model=model,
            route_id=spec.model.route_id,
            api_format=api_format,
            credential_managed=credential_managed,
        )
        if module_prefix:
            env_name = "DEEPAGENTS_MODEL_" + module_prefix.replace(".", "_").upper().strip("_")
            env_example = env_example.replace("DEEPAGENTS_MODEL", env_name)
            readme = readme.replace("DEEPAGENTS_MODEL", env_name)
        _write(archive, ".env.example", env_example)
        _write(archive, "README.md", readme)
        _write(
            archive,
            "agent-studio.json",
            json.dumps(extensions, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        _write(archive, "agent.py", agent_py)
        _write(archive, "langgraph.json", _render_langgraph_json(spec.name))
        _write(archive, ".gitignore", _GITIGNORE)
        if spec.mcp_servers:
            _write(archive, "mcp_servers.py", _render_mcp_module(mcp_plans))
        _write(archive, "tools/__init__.py", "")
        _write(archive, "tools/operators/__init__.py", "")
        for tool in spec.python_tools:
            _write(archive, f"tools/{tool.name}.py", _render_tool_module(tool))
            _write(archive, f"tools/operators/{tool.name}.py", tool.code)
        for skill in resolved_skills:
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
        _write(archive, "subagents/__init__.py", "")
        for item in spec.subagents:
            package = _python_identifier(item.alias)
            child = children[item.ref]
            child_archive = export_deepagents_project(
                child, mcp_capabilities=capabilities,
                model_route=(model_routes or {}).get(child.spec.model.route_id),
                module_prefix=f"{module_prefix}subagents.{package}.",
            )
            _write(archive, f"subagents/{package}/__init__.py", "")
            with ZipFile(io.BytesIO(child_archive.content)) as nested:
                for entry in nested.namelist():
                    _write(archive, f"subagents/{package}/{entry}", nested.read(entry))


    return DeepagentsProjectArchive(
        content=output.getvalue(),
        filename=f"{spec.name}-{spec.version}-deepagents.zip",
    )
