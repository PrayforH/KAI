import ast
import base64
import json
from datetime import UTC, datetime
from io import BytesIO
from zipfile import ZipFile

from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.deepagents_export import (
    DEEPAGENTS_PINNED_VERSION,
    export_deepagents_project,
)
from harness.studio.factory import create_draft_spec
from harness.studio.models import (
    AgentDraft,
    AgentTemplate,
    CapabilityRisk,
    DraftPythonTool,
    DraftSkill,
    DraftSkillFile,
    DraftSubagent,
    McpCapability,
    NetworkAccess,
)

NOW = datetime(2026, 7, 16, tzinfo=UTC)

TAVILY = McpCapability(
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


def make_draft(**spec_updates: object) -> AgentDraft:
    spec = create_draft_spec(
        name="invoice-reviewer",
        domain="accounts-payable",
        display_name="发票审核助手",
        description="核对发票证据并标记需要人工确认的例外。",
        template=AgentTemplate.ANALYST,
    )
    if spec_updates:
        spec = spec.model_copy(update=spec_updates)
    return AgentDraft(
        draftId="draft_test",
        tenantId="tenant-a",
        revision=1,
        spec=spec,
        createdBy="builder-a",
        updatedBy="builder-a",
        createdAt=NOW,
        updatedAt=NOW,
    )


def _names(archive: bytes) -> set[str]:
    with ZipFile(BytesIO(archive)) as bundle:
        return set(bundle.namelist())


def _read(archive: bytes, name: str) -> str:
    with ZipFile(BytesIO(archive)) as bundle:
        return bundle.read(name).decode()


def test_export_pins_runtime_and_carries_default_assets() -> None:
    source = make_draft(builtin_tools=("Read", "Glob", "Grep", "Write"))

    exported = export_deepagents_project(source)

    assert exported.filename == "invoice-reviewer-0.1.0-deepagents.zip"
    names = _names(exported.content)
    assert {
        "pyproject.toml",
        "README.md",
        ".env.example",
        ".gitignore",
        "agent-studio.json",
        "src/sapling_deep_agents/agents/agent.py",
        "langgraph.json",
        "src/sapling_deep_agents/tools/__init__.py",
        "src/sapling_deep_agents/agents/subagents/__init__.py",
    } <= names
    assert "src/sapling_deep_agents/middleware/mcp_servers.py" not in names

    pyproject = _read(exported.content, "pyproject.toml")
    assert f'"deepagents=={DEEPAGENTS_PINNED_VERSION}"' in pyproject
    assert '"python-dotenv>=1.0,<2.0"' in pyproject

    agent_py = _read(exported.content, "src/sapling_deep_agents/agents/agent.py")
    assert "TodoListMiddleware()" in agent_py
    assert "FilesystemMiddleware(backend=BACKEND, tools=_FILESYSTEM_TOOLS," in agent_py
    # .env loading must run before MODEL / MCP credential resolution.
    settings = _read(exported.content, "src/sapling_deep_agents/config/settings.py")
    assert 'load_dotenv(PROJECT_ROOT / ".env")' in settings
    assert settings.index("load_dotenv(") < settings.index("MODEL = ")
    # Only answer blocks reach stdout; reasoning blocks must not leak.
    # No bespoke CLI: langgraph.json is the entry point.
    assert "main.py" not in names
    # No Bash -> no execution backend, and `delete` never ships.
    assert "FilesystemBackend(root_dir=WORKSPACE_ROOT)" in agent_py
    assert "LocalShellBackend" not in agent_py
    assert "'execute'" not in agent_py
    assert "'delete'" not in agent_py

    extensions = json.loads(_read(exported.content, "agent-studio.json"))
    assert extensions["deepagentsVersion"] == DEEPAGENTS_PINNED_VERSION
    assert extensions["addedExportTools"] == ["ls"]

    # langgraph.json is the project's entry point and must stay loadable by
    # langgraph_api: a synchronous zero-arg factory, no self-managed persistence.
    langgraph = json.loads(_read(exported.content, "langgraph.json"))
    assert langgraph == {
        "dependencies": ["."],
        "graphs": {"invoice-reviewer": "./agent.py:agent"},
        "env": ".env",
    }
    assert "def agent():" in agent_py
    assert "def build_agent(model=None):" in agent_py
    # LangGraph API owns persistence; a self-managed saver breaks `langgraph dev`.
    assert "checkpointer=" not in agent_py
    assert "InMemorySaver" not in agent_py
    assert "main.py" not in names
    # macOS python.org installs have no bare `python`; the quick start must
    # use python3 to create the venv. PyCharm can create a venv without pip,
    # and langgraph dev requires the project installed into that interpreter.
    readme = _read(exported.content, "README.md")
    assert "python3 -m venv .venv" in readme
    assert "python -m ensurepip --upgrade" in readme
    assert "python -m pip install -e ." in readme
    gitignore = _read(exported.content, ".gitignore")
    assert ".idea/" in gitignore and ".langgraph_api/" in gitignore
    assert extensions["filesystemTools"] == [
        "ls",
        "read_file",
        "glob",
        "grep",
        "write_file",
    ]
    assert extensions["droppedSemantics"]["平台记忆"]
    assert "知识库" not in extensions["droppedSemantics"]

    # Generated python must at least parse.
    for name in names:
        if name.endswith(".py"):
            ast.parse(_read(exported.content, name))


def test_export_maps_bash_branch_permissions_and_subagents() -> None:
    skill = DraftSkill(
        name="invoice-reviewer-core",
        description="Review invoice evidence.",
        instructions="Verify the invoice before reporting a result.",
        files=(
            DraftSkillFile(
                path="assets/template.png",
                contentBase64=base64.b64encode(b"\x89PNG").decode("ascii"),
            ),
        ),
    )
    tool = DraftPythonTool(
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
    source = make_draft(
        builtin_tools=("Read", "Write", "Bash", "Task"),
        python_tools=(tool,),
        skills=(skill,),
        permission_policy="production-read-only",
        subagents=(
            DraftSubagent(
                alias="fact-researcher",
                ref="helper-agent@1.0.0",
                responsibility="只读核验事实、来源和证据缺口。",
                background=True,
            ),
        ),
    )

    exported = export_deepagents_project(
        source,
        subagent_drafts={
            "helper-agent@1.0.0": make_draft(
                name="helper-agent", version="1.0.0", system_prompt="真实固定子智能体提示词"
            )
        },
    )

    agent_py = _read(exported.content, "src/sapling_deep_agents/agents/agent.py")
    # LangGraph API owns persistence: a self-managed saver makes `langgraph dev`
    # refuse the graph, so none may be emitted. The comment may mention the word.
    assert "checkpointer=" not in agent_py
    assert "InMemorySaver" not in agent_py
    assert "def agent():" in agent_py
    # Bash trades permissions away (0.7.13 NotImplementedError), even read-only.
    assert "LocalShellBackend(root_dir=WORKSPACE_ROOT" in agent_py
    assert "FilesystemPermission" not in agent_py
    assert "'execute'" in agent_py
    assert "build_fact_researcher(model=model)" in agent_py
    # Read-only + Bash -> an approval gate; the platform checkpointer resumes it.
    assert 'interrupt_on={"execute": True, "write_file": True, "edit_file": True},' in agent_py

    subagent_py = _read(
        exported.content, "src/sapling_deep_agents/agents/subagents/fact_researcher/agents/agent.py"
    )
    assert "真实固定子智能体提示词" in _read(
        exported.content,
        "src/sapling_deep_agents/agents/subagents/fact_researcher/prompts/system.md",
    )
    assert "NoSubagentsMiddleware" in subagent_py

    skill_md = _read(
        exported.content, "src/sapling_deep_agents/skills/invoice-reviewer-core/SKILL.md"
    )
    assert "name: invoice-reviewer-core" in skill_md
    with ZipFile(BytesIO(exported.content)) as bundle:
        assert (
            bundle.read("src/sapling_deep_agents/skills/invoice-reviewer-core/assets/template.png")
            == b"\x89PNG"
        )

    tool_py = _read(exported.content, "src/sapling_deep_agents/tools/normalize_score.py")
    assert "def run(arguments):" in _read(
        exported.content, "src/sapling_deep_agents/tools/operators/normalize_score.py"
    )
    assert '"value"' in tool_py

    for name in _names(exported.content):
        if name.endswith(".py"):
            ast.parse(_read(exported.content, name))


def test_export_declares_dropped_knowledge_and_mcp_env_placeholders() -> None:
    source = make_draft(
        knowledge_references=("kb-policy",),
        mcp_servers=("tavily-readonly",),
    )

    exported = export_deepagents_project(
        source,
        mcp_capabilities={"tavily-readonly": TAVILY},
    )

    assert "src/sapling_deep_agents/middleware/mcp_servers.py" in _names(exported.content)
    mcp_py = _read(exported.content, "src/sapling_deep_agents/middleware/mcp_servers.py")
    assert "MultiServerMCPClient" in mcp_py
    # The connector module must never shadow the third-party mcp SDK.
    assert '"mcp"' not in _read(exported.content, "pyproject.toml")
    assert "from sapling_deep_agents.middleware.mcp_servers import McpToolsMiddleware" in _read(
        exported.content, "src/sapling_deep_agents/agents/agent.py"
    )
    assert "McpToolsMiddleware()," in _read(
        exported.content, "src/sapling_deep_agents/agents/agent.py"
    )
    assert "class McpToolsMiddleware(AgentMiddleware)" in mcp_py
    # Endpoint is exported; the query credential is an env placeholder.
    assert "https://mcp.tavily.com/mcp/?tavilyApiKey=$__ENV__TAVILY_API_KEY__" in mcp_py

    env_example = _read(exported.content, ".env.example")
    assert "TAVILY_API_KEY" in env_example

    extensions = json.loads(_read(exported.content, "agent-studio.json"))
    assert extensions["knowledgeReferences"] == ["kb-policy"]
    assert "知识库" in extensions["droppedSemantics"]
    assert extensions["deepagentsModel"] == "openai:deepseek-v4-pro"

    readme = _read(exported.content, "README.md")
    assert "TAVILY_API_KEY" in readme
    assert "不能检索知识库" in readme


def test_export_uses_route_api_format_for_provider_and_declares_managed_credentials() -> None:
    """The route catalog, not the model name, decides the wire protocol.

    Production routes are `anthropic_compatible` aliases such as
    `deepseek-v4-flash`, so a name-prefix guess would emit the wrong provider
    and an .env that cannot authenticate.
    """

    source = make_draft()
    route = next(
        item
        for item in default_capability_catalog().model_routes
        if item.route_id == source.spec.model.route_id
    )
    assert route.api_format == "anthropic_compatible"

    exported = export_deepagents_project(source, model_route=route)

    settings = _read(exported.content, "src/sapling_deep_agents/config/settings.py")
    assert f"'anthropic:{source.spec.model.model}'" in settings
    assert "'openai:" not in settings

    env_example = _read(exported.content, ".env.example")
    # Only the active provider's credential is presented as required.
    assert "# ANTHROPIC_API_KEY=<你的 key>" in env_example
    assert "# OPENAI_API_KEY=" not in env_example
    # Managed platform credentials are never exportable; say so.
    assert "平台托管" in env_example
    assert "apiFormat=anthropic_compatible" in env_example

    extensions = json.loads(_read(exported.content, "agent-studio.json"))
    assert extensions["deepagentsProvider"] == "anthropic"
    assert extensions["routeApiFormat"] == "anthropic_compatible"
    assert extensions["routeCredentialManaged"] is True
    assert "平台托管" in _read(exported.content, "README.md") or (
        "凭据不随导出提供" in _read(exported.content, "README.md")
    )


def test_export_without_route_falls_back_to_name_heuristic() -> None:
    exported = export_deepagents_project(make_draft())

    # deepseek is not a recognised prefix, so the fallback stays on openai and
    # the OpenAI-compatible credential pair is presented.
    env_example = _read(exported.content, ".env.example")
    assert "# OPENAI_API_KEY=<你的 key>" in env_example
    assert "# ANTHROPIC_API_KEY=" not in env_example
    extensions = json.loads(_read(exported.content, "agent-studio.json"))
    assert extensions["routeApiFormat"] is None
    assert extensions["routeCredentialManaged"] is False


def test_export_is_deterministic() -> None:
    source = make_draft(
        mcp_servers=("tavily-readonly",),
        subagents=(
            DraftSubagent(
                alias="fact-researcher",
                ref="helper-agent@1.0.0",
                responsibility="只读核验事实。",
            ),
        ),
    )

    first = export_deepagents_project(
        source,
        mcp_capabilities={"tavily-readonly": TAVILY},
        subagent_drafts={"helper-agent@1.0.0": make_draft(name="helper-agent", version="1.0.0")},
    )
    second = export_deepagents_project(
        source,
        mcp_capabilities={"tavily-readonly": TAVILY},
        subagent_drafts={"helper-agent@1.0.0": make_draft(name="helper-agent", version="1.0.0")},
    )

    assert first.content == second.content
    assert first.filename == second.filename


def test_export_resolves_skill_references_like_the_compiler() -> None:
    """Resolved skills flow through the service, mirroring publish materialization."""

    compiler = AgentDraftCompiler(default_capability_catalog())
    source = make_draft(skill_references=("frontend-design",))
    resolved = compiler.resolve_skills(source)
    assert resolved, "default catalog must resolve the reference for this contract"

    exported = export_deepagents_project(source, skills=resolved)

    assert any(
        name.startswith("src/sapling_deep_agents/skills/") for name in _names(exported.content)
    )


def test_export_refuses_unresolved_fixed_children() -> None:
    import pytest

    from harness.core.errors import ConflictError

    source = make_draft(
        subagents=(
            DraftSubagent(
                alias="reviewer",
                ref="missing@1.0.0",
                responsibility="Review evidence",
            ),
        )
    )
    with pytest.raises(ConflictError, match="固定版本子智能体"):
        export_deepagents_project(source)


def test_export_refuses_uninstallable_project_version() -> None:
    import pytest

    from harness.core.errors import ConflictError

    with pytest.raises(ConflictError, match="版本号"):
        export_deepagents_project(make_draft(version="invalid-version"))


def test_export_scaffold_is_installable_and_contains_no_duplicate_entries() -> None:
    import tomllib

    exported = export_deepagents_project(make_draft())
    with ZipFile(BytesIO(exported.content)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert {
            "agent.py",
            "MANIFEST.in",
            "docker/Dockerfile",
            "docker/compose.yaml",
            ".dockerignore",
            ".gitlab-ci.yml",
            ".pre-commit-config.yaml",
            "AGENTS.md",
            "pyrightconfig.json",
            "test/test_project.py",
            "src/sapling_deep_agents/controller/agent.py",
            "src/sapling_deep_agents/run/app.py",
            "src/sapling_deep_agents/services/assets.py",
            "src/sapling_deep_agents/prompts/system.md",
        } <= set(names)
        project = tomllib.loads(archive.read("pyproject.toml").decode())
        assert project["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
        assert "py-modules" not in project["tool"]["setuptools"]
        assert archive.read("src/sapling_deep_agents/prompts/system.md").decode() == (
            make_draft().spec.system_prompt
        )
        # The root shim must import the installed package, not mutate sys.path.
        assert (
            "from sapling_deep_agents.controller.agent import" in archive.read("agent.py").decode()
        )
        assert "uv.lock" not in names  # Resolve on the target package index, never fake a lock.
