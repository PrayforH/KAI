"""Opt-in real runtime contract: DEEPAGENTS_TEST_PYTHON points to an isolated 0.7.13 venv.

Keep DeepAgents outside Harness dependencies. Run with the generated project's declared
requirements installed; fake model/MCP responses exercise execution without API charges.
"""

import os
import subprocess
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from harness.studio.deepagents_export import export_deepagents_project
from harness.studio.models import DraftPythonTool, DraftSkill, DraftSubagent
from tests.unit.studio.test_deepagents_export import TAVILY, make_draft

RUNTIME_PYTHON = os.environ.get("DEEPAGENTS_TEST_PYTHON")
pytestmark = pytest.mark.skipif(not RUNTIME_PYTHON, reason="isolated DeepAgents runtime required")


def run_project(path: Path, code: str) -> None:
    result = subprocess.run(
        [RUNTIME_PYTHON or "python", "-c", code],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=90,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if key
                not in {"PYTHONPATH", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"}
            },
            "PYTHONPATH": str(path / "src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr


MODEL = """
import asyncio
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
BOUND = []
class Recorder(BaseChatModel):
    @property
    def _llm_type(self): return "recorder"
    def bind_tools(self, tools, **kwargs):
        BOUND[:] = list(tools)
        return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=reply(messages))])
"""


def test_real_python_schema_mcp_execution_and_permissions(tmp_path: Path) -> None:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is-valid": {"type": "boolean", "default": True},
            "optional": {"type": ["string", "null"], "default": None},
            "nested": {"type": "object", "properties": {"kind": {"enum": ["ok"]}}},
        },
        "required": ["is-valid", "nested"],
    }
    source = make_draft(
        builtin_tools=("Read", "Write", "Edit"),
        permission_policy="production-read-only",
        mcp_servers=(TAVILY.reference,),
        python_tools=(
            DraftPythonTool(
                name="echo_args",
                description="Return schema arguments",
                inputSchema=schema,
                code=(
                    "from __future__ import annotations\n\n"
                    "def run(arguments):\n    return arguments\n"
                ),
            ),
        ),
    )
    archive = export_deepagents_project(source, mcp_capabilities={TAVILY.reference: TAVILY})
    with ZipFile(BytesIO(archive.content)) as bundle:
        bundle.extractall(tmp_path)
    run_project(
        tmp_path,
        MODEL
        + """
from sapling_deep_agents.agents import agent
from sapling_deep_agents.middleware import mcp_servers
from langchain_core.tools import StructuredTool
from jsonschema import ValidationError
from sapling_deep_agents.tools.echo_args import build, INPUT_SCHEMA
from sapling_deep_agents.tools.operators.echo_args import run
args = {"is-valid": True, "optional": None, "nested": {"kind": "ok"}}
tool = build()
assert tool.args_schema == INPUT_SCHEMA
assert asyncio.run(tool.ainvoke(args)) == args
try:
    asyncio.run(tool.ainvoke({**args, "nested": {"kind": "invalid"}}))
except ValidationError:
    pass
else:
    raise AssertionError("Nested schema constraints must be enforced")
CALLS = []
async def search(query: str) -> str:
    CALLS.append(query)
    return "verified evidence"
class Client:
    def __init__(self, connections): pass
    async def get_tools(self, *, server_name):
        return [StructuredTool.from_function(coroutine=search, name="tavily_search",
                    description="Search evidence"),
                StructuredTool.from_function(coroutine=search, name="forbidden",
                    description="Not in Studio catalog")]
mcp_servers.MultiServerMCPClient = Client
import os
os.environ["TAVILY_API_KEY"] = "test&key=?#"
from urllib.parse import parse_qs, urlsplit
connection = mcp_servers.resolved_connections()["tavily-readonly"]
assert parse_qs(urlsplit(connection["url"]).query)["tavilyApiKey"] == ["test&key=?#"]
def reply(messages):
    if any(isinstance(m, ToolMessage) for m in messages):
        assert "verified evidence" in str(messages[-1].content)
        return AIMessage(content="done")
    return AIMessage(content="", tool_calls=[{"name":"mcp__tavily__tavily_search",
                       "args":{"query":"evidence"}, "id":"call1", "type":"tool_call"}])
graph = agent.build_agent(model=Recorder())
result = asyncio.run(graph.ainvoke({"messages":[{"role":"user", "content":"search"}]}))
assert result["messages"][-1].content == "done"
assert CALLS == ["evidence"]
assert {t.name for t in BOUND} == {"ls", "read_file", "write_file", "edit_file",
                                  "write_todos", "echo_args", "mcp__tavily__tavily_search"}
assert graph.config["recursion_limit"] == agent.RECURSION_LIMIT
# Exercise read-only enforcement through the graph's tool path.
def reply(messages):
    if any(isinstance(m, ToolMessage) for m in messages):
        return AIMessage(content="done")
    return AIMessage(content="", tool_calls=[{"name":"write_file",
                      "args":{"file_path":"/denied.txt", "content":"forbidden"},
                      "id":"write1", "type":"tool_call"}])
asyncio.run(graph.ainvoke({"messages":[{"role":"user", "content":"write"}]}))
assert not (agent.WORKSPACE_ROOT / "denied.txt").exists()
""",
    )


def test_real_fixed_subagent_delegation_and_assets(tmp_path: Path) -> None:
    child = make_draft(
        name="child",
        version="1.0.0",
        system_prompt="IMMUTABLE_CHILD_PROMPT",
        builtin_tools=("Read",),
        skills=(
            DraftSkill(
                name="child-evidence",
                description="Child evidence skill",
                instructions="Use only immutable evidence.",
            ),
        ),
        python_tools=(
            DraftPythonTool(
                name="child_tool",
                description="Child operator",
                inputSchema={"type": "object", "properties": {}},
                code="def run(arguments):\n    return {'child': True}\n",
            ),
        ),
    )
    parent = make_draft(
        subagents=(
            DraftSubagent(
                alias="reviewer", ref="child@1.0.0", responsibility="Review fixed evidence"
            ),
        )
    )
    archive = export_deepagents_project(parent, subagent_drafts={"child@1.0.0": child})
    with ZipFile(BytesIO(archive.content)) as bundle:
        bundle.extractall(tmp_path)
    run_project(
        tmp_path,
        MODEL
        + """
from sapling_deep_agents.agents import agent
SEEN_CHILD = []
def reply(messages):
    if "IMMUTABLE_CHILD_PROMPT" in str(messages[0].content):
        assert "child-evidence" in str(messages[0].content)
        assert {t.name for t in BOUND} == {"ls", "read_file", "write_todos", "child_tool"}
        SEEN_CHILD.append(True)
        return AIMessage(content="child verified evidence")
    if any(isinstance(m, ToolMessage) for m in messages):
        assert "child verified evidence" in str(messages[-1].content)
        return AIMessage(content="parent complete")
    return AIMessage(content="", tool_calls=[{"name":"task",
        "args":{"subagent_type":"reviewer", "description":"verify"},
        "id":"child1", "type":"tool_call"}])
graph = agent.build_agent(model=Recorder())
result = asyncio.run(graph.ainvoke({"messages":[{"role":"user", "content":"review"}]}))
assert result["messages"][-1].content == "parent complete"
assert SEEN_CHILD == [True]
from sapling_deep_agents.agents.subagents.reviewer.tools.child_tool import build
assert asyncio.run(build().ainvoke({})) == {"child": True}
""",
    )


def test_real_bash_readonly_requires_approval(tmp_path: Path) -> None:
    source = make_draft(
        builtin_tools=("Read", "Write", "Edit", "Bash"), permission_policy="production-read-only"
    )
    with ZipFile(BytesIO(export_deepagents_project(source).content)) as bundle:
        bundle.extractall(tmp_path)
    run_project(
        tmp_path,
        MODEL
        + """
from sapling_deep_agents.agents import agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
def reply(messages):
    if any(isinstance(m, ToolMessage) for m in messages):
        return AIMessage(content="done")
    return AIMessage(content="", tool_calls=[{"name":"execute",
        "args":{"command":"printf checked > marker.txt"}, "id":"exec1", "type":"tool_call"}])
graph = agent.build_agent(model=Recorder())
# LangGraph API normally supplies this; inject one for this headless contract test.
graph.checkpointer = InMemorySaver()
config = {"configurable":{"thread_id":"approval-smoke"}}
result = asyncio.run(graph.ainvoke({"messages":[{"role":"user", "content":"write"}]}, config))
assert result["__interrupt__"]
assert not (agent.WORKSPACE_ROOT / "marker.txt").exists()
result = asyncio.run(graph.ainvoke(Command(resume={"decisions":[{"type":"approve"}]}), config))
assert result["messages"][-1].content == "done"
assert (agent.WORKSPACE_ROOT / "marker.txt").exists(), repr(result["messages"])
assert (agent.WORKSPACE_ROOT / "marker.txt").read_text() == "checked"
""",
    )


def test_settings_load_root_env_before_children_and_keep_workspaces_separate(
    tmp_path: Path,
) -> None:
    child = make_draft(name="child", version="1.0.0")
    parent = make_draft(
        subagents=(
            DraftSubagent(
                alias="reviewer",
                ref="child@1.0.0",
                responsibility="Review evidence",
            ),
        )
    )
    archive = export_deepagents_project(parent, subagent_drafts={"child@1.0.0": child})
    with ZipFile(BytesIO(archive.content)) as bundle:
        assert len(bundle.namelist()) == len(set(bundle.namelist()))
        bundle.extractall(tmp_path)
    (tmp_path / ".env").write_text(
        "DEEPAGENTS_MODEL=openai:parent-env\n"
        "DEEPAGENTS_MODEL_SUBAGENTS_REVIEWER=openai:child-env\n",
        encoding="utf-8",
    )
    run_project(
        tmp_path,
        """
from pathlib import Path
from sapling_deep_agents.agents import agent
from sapling_deep_agents.agents.subagents.reviewer.agents import agent as child
assert agent.MODEL == "openai:parent-env"
assert child.MODEL == "openai:child-env"
assert agent.WORKSPACE_ROOT == Path.cwd() / "workspace"
assert child.WORKSPACE_ROOT == agent.WORKSPACE_ROOT / "agents/subagents/reviewer"
assert not any((Path.cwd() / "src").rglob("workspace"))
""",
    )


def test_built_wheel_imports_assets_outside_source_tree(tmp_path: Path) -> None:
    # The opt-in runtime environment needs the generated project's [dev] extra
    # plus setuptools/wheel, so the actual build can run without network access.
    from harness.studio.models import DraftSkillFile

    asset = DraftSkill(
        name="wheel-evidence",
        description="Binary wheel fixture",
        instructions="Read evidence.",
        files=(
            DraftSkillFile(path="assets/evidence.bin", contentBase64="AP9QTkc="),
            DraftSkillFile(path=".config/rules.txt", content="hidden asset"),
        ),
    )
    child = make_draft(name="child", version="1.0.0", skills=(asset,))
    parent = make_draft(
        skills=(asset,),
        subagents=(
            DraftSubagent(
                alias="reviewer",
                ref="child@1.0.0",
                responsibility="Review evidence",
            ),
        ),
    )
    project = tmp_path / "project"
    with ZipFile(
        BytesIO(
            export_deepagents_project(
                parent,
                subagent_drafts={"child@1.0.0": child},
            ).content
        )
    ) as archive:
        archive.extractall(project)
    build = subprocess.run(
        [RUNTIME_PYTHON or "python", "-m", "build", "--wheel", "--no-isolation"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    site = tmp_path / "wheel-site"
    with ZipFile(next((project / "dist").glob("*.whl"))) as wheel:
        wheel.extractall(site)
    outside = tmp_path / "outside"
    outside.mkdir()
    code = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(site)!r})
import sapling_deep_agents
from sapling_deep_agents.agents import agent
from sapling_deep_agents.agents.subagents.reviewer.agents import agent as child
assert str(sapling_deep_agents.__file__).startswith({str(site)!r})
assert agent.SYSTEM_PROMPT == {parent.spec.system_prompt!r}
assert child.SYSTEM_PROMPT == {child.spec.system_prompt!r}
for root in (agent.WORKSPACE_ROOT, child.WORKSPACE_ROOT):
    assert root.is_relative_to(Path.cwd())
    assert (root / "skills/wheel-evidence/assets/evidence.bin").read_bytes() == b"\\x00\\xffPNG"
    assert (root / "skills/wheel-evidence/.config/rules.txt").read_text() == "hidden asset"
from sapling_deep_agents.services.assets import materialize_skills
before = {{p: p.stat().st_mtime_ns for p in agent.WORKSPACE_ROOT.rglob("*") if p.is_file()}}
user_file = agent.WORKSPACE_ROOT / "skills/user-notes.txt"
user_file.write_text("Keep my notes")
materialize_skills(agent.WORKSPACE_ROOT)
assert all(p.stat().st_mtime_ns == mtime for p, mtime in before.items())
assert user_file.read_text() == "Keep my notes"
assert not any(Path({str(site)!r}).rglob("workspace"))
"""
    result = subprocess.run(
        [RUNTIME_PYTHON or "python", "-I", "-c", code],
        cwd=outside,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
