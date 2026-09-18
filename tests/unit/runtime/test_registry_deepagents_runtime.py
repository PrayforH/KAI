"""The registry wrapper decides everything before a graph is assembled.

These are the guards that keep a DeepAgents Run honest: a Python tool the kernel
cannot execute is refused rather than silently dropped, a Bundle operator keeps
the canonical name the policy engine and quota ledger use, and only streamable
HTTP MCP registrations become connections.

The kernel ships as an optional extra, so the module under test is skipped
rather than failing the suite when it is not installed. The plan and the stream
mapper are deliberately import-free and stay covered either way.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

pytest.importorskip("deepagents", reason="the DeepAgents kernel is an optional extra")

from harness.core.manifest import AgentManifestSnapshot, PythonToolSnapshot  # noqa: E402
from harness.runtime.base import RuntimeContext  # noqa: E402
from harness.runtime.deepagents_tool_gate import canonical_deepagents_tool  # noqa: E402
from harness.runtime.registry_deepagents_runtime import (  # noqa: E402
    _bundle_operators,
    _reject_unservable_python_tools,
    _stage_bundle_operators,
    _streamable_http_servers,
)
from harness.runtime.tools import ResolvedTools, ToolResolutionError  # noqa: E402

_TOOL_SOURCE = b"def run(arguments):\n    return {'content': []}\n"


def _python_tool(name: str) -> PythonToolSnapshot:
    return PythonToolSnapshot(
        reference=f"bundle:tools/{name}.py",
        path=f"tools/{name}.py",
        name=name,
        description=f"{name} operator",
        input_schema={"type": "object"},
        content_base64=base64.b64encode(_TOOL_SOURCE).decode(),
        sha256=hashlib.sha256(_TOOL_SOURCE).hexdigest(),
        size_bytes=len(_TOOL_SOURCE),
    )


def _snapshot(*, tools: list[dict[str, str]], bundle: bool = False) -> AgentManifestSnapshot:
    return AgentManifestSnapshot.model_validate(
        {
            "manifest": {
                "apiVersion": "harness/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "score-agent", "version": "1.0.0"},
                "spec": {
                    "runtime": "deepagents",
                    "model": {"route": "default", "model": "gateway-model"},
                    "prompt": {"system": "prompts/system.md"},
                    "tools": tools,
                    "permissions": {"policy": "default"},
                },
            },
            "system_prompt": "You are a test.",
            "python_tool_snapshots": (
                [_python_tool("score").model_dump(mode="json")] if bundle else []
            ),
            "content_hash": "a" * 64,
        }
    )


def _resolved(*allowed: str) -> ResolvedTools:
    return ResolvedTools(
        builtin_tools=("Read",),
        mcp_servers={},
        allowed_tools=tuple(allowed),
        mcp_smokes={},
    )


def _context(workspace: Path, *, executor: object | None) -> RuntimeContext:
    """A stand-in exposing only the two attributes the staging pass reads."""

    return cast(
        RuntimeContext,
        SimpleNamespace(workspace=workspace, sandbox_command_executor=executor),
    )


def test_staging_without_operators_touches_no_workspace(tmp_path: Path) -> None:
    """A manifest that publishes no Bundle operator must not pay for a write."""

    staged = _stage_bundle_operators(
        _snapshot(tools=[{"builtin": "Read"}]),
        _context(tmp_path, executor=None),
    )

    assert staged.overrides == {}
    assert staged.materialized == {}
    assert list(tmp_path.iterdir()) == []


def test_staging_without_an_executor_is_refused(tmp_path: Path) -> None:
    """A Bundle operator with no Sandbox would have to run in the Worker."""

    with pytest.raises(ToolResolutionError, match="isolated Sandbox execution"):
        _stage_bundle_operators(
            _snapshot(tools=[{"python": "bundle:tools/score.py"}], bundle=True),
            _context(tmp_path, executor=None),
        )


def test_staging_yields_both_views_from_one_pass(tmp_path: Path) -> None:
    """The resolver's overrides and the runtime's paths must not drift apart."""

    staged = _stage_bundle_operators(
        _snapshot(tools=[{"python": "bundle:tools/score.py"}], bundle=True),
        _context(tmp_path, executor=object()),
    )

    assert set(staged.overrides) == {"bundle:tools/score.py"}
    assert set(staged.materialized) == {"bundle:tools/score.py"}


def test_in_process_python_tools_are_refused_not_dropped() -> None:
    """DeepAgents has no in-process tool plane; running one would need the Worker."""

    snapshot = _snapshot(tools=[{"python": "mypackage:my_tool"}])

    with pytest.raises(ToolResolutionError, match="in-process Python tool plane"):
        _reject_unservable_python_tools(snapshot)


def test_bundle_operators_are_accepted() -> None:
    _reject_unservable_python_tools(
        _snapshot(tools=[{"python": "bundle:tools/score.py"}], bundle=True)
    )


def test_bundle_operators_take_the_published_canonical_name() -> None:
    """The name comes from the tool directory, not from a prefix rule."""

    snapshot = _snapshot(tools=[{"python": "bundle:tools/score.py"}], bundle=True)
    operators = _bundle_operators(
        snapshot,
        _resolved("Read", "mcp__harness-python-score-agent__score"),
        materialized={"bundle:tools/score.py": Path("x/bundle-tools/score.py")},
    )

    assert len(operators) == 1
    assert operators[0].name == "mcp__harness-python-score-agent__score"
    assert operators[0].path == Path("x/bundle-tools/score.py")
    assert operators[0].tool.name == "score"


def test_a_bundle_operator_missing_from_the_directory_is_refused() -> None:
    snapshot = _snapshot(tools=[{"python": "bundle:tools/score.py"}], bundle=True)

    with pytest.raises(ToolResolutionError, match="published tool directory"):
        _bundle_operators(
            snapshot,
            _resolved("Read"),
            materialized={"bundle:tools/score.py": Path("x/bundle-tools/score.py")},
        )


def test_an_unmaterialized_bundle_operator_is_refused() -> None:
    snapshot = _snapshot(tools=[{"python": "bundle:tools/score.py"}], bundle=True)

    with pytest.raises(ToolResolutionError, match="not materialized"):
        _bundle_operators(
            snapshot,
            _resolved("mcp__harness-python-score-agent__score"),
            materialized={},
        )


def test_only_streamable_http_becomes_an_mcp_connection() -> None:
    resolved = ResolvedTools(
        builtin_tools=(),
        mcp_servers={
            "knowledge": {"type": "http", "url": "https://mcp.example/mcp"},
            # The staged Bundle operators; DeepAgents serves them natively.
            "harness-python-score-agent": {"type": "sdk", "name": "x", "instance": object()},
        },
        allowed_tools=(),
        mcp_smokes={},
    )

    connections = _streamable_http_servers(resolved)

    assert set(connections) == {"knowledge"}
    assert connections["knowledge"]["url"] == "https://mcp.example/mcp"


def test_a_non_http_mcp_transport_is_refused() -> None:
    resolved = ResolvedTools(
        builtin_tools=(),
        mcp_servers={"legacy": {"type": "sse", "url": "https://mcp.example/sse"}},
        allowed_tools=(),
        mcp_smokes={},
    )

    with pytest.raises(ToolResolutionError, match="streamable HTTP"):
        _streamable_http_servers(resolved)


def test_an_http_registration_without_a_url_is_refused() -> None:
    resolved = ResolvedTools(
        builtin_tools=(),
        mcp_servers={"broken": {"type": "http"}},
        allowed_tools=(),
        mcp_smokes={},
    )

    with pytest.raises(ToolResolutionError, match="endpoint is invalid"):
        _streamable_http_servers(resolved)


def test_tool_names_are_translated_into_the_policy_vocabulary() -> None:
    """Policy rules, quotas and containment are all written on platform names."""

    assert canonical_deepagents_tool("execute") == "Bash"
    assert canonical_deepagents_tool("write_file") == "Write"
    assert canonical_deepagents_tool("edit_file") == "Edit"
    assert canonical_deepagents_tool("read_file") == "Read"
    assert canonical_deepagents_tool("grep") == "Grep"
    # `ls` has no platform builtin; it inherits read-only discovery.
    assert canonical_deepagents_tool("ls") == "Glob"
    # Platform web tools keep their builtin identity so policy can name them.
    assert canonical_deepagents_tool("mcp__harness-web__search") == "WebSearch"
    # Published MCP and Bundle tools keep the canonical platform spelling.
    assert canonical_deepagents_tool("mcp__foo__bar") == "mcp__foo__bar"
    assert (
        canonical_deepagents_tool("mcp__harness-python-score-agent__score")
        == "mcp__harness-python-score-agent__score"
    )
