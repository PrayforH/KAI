"""RuntimeCapabilities v0 contract.

One shared fixture (``tests/fixtures/runtime/runtime_capabilities_v0.json``)
is the single source of truth verified against three consumers at once:

1. the compiler's runtime conclusions (this file),
2. the runtime registration set composed into workers and API processes,
3. the Builder display contract consumed by the web console — mirrored in
   ``web/harness-console/tests/runtime-capabilities-contract.spec.ts``.

The fixture-freshness test keeps the file in lockstep with the default
capability catalog; the frontend spec reads the same file without importing
any Python.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from harness.runtime.installed import INSTALLED_AGENT_RUNTIMES
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.factory import create_draft_spec
from harness.studio.models import (
    AgentDraft,
    AgentTemplate,
    DraftPythonTool,
    ModelRouteCapability,
    RuntimeCapability,
    ValidationSeverity,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "runtime" / "runtime_capabilities_v0.json"
)
NOW = datetime(2026, 8, 31, tzinfo=UTC)


def load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


def fixture_runtimes() -> tuple[RuntimeCapability, ...]:
    return tuple(RuntimeCapability.model_validate(item) for item in load_fixture())


def fixture_catalog(openai_route: ModelRouteCapability) -> object:
    catalog = default_capability_catalog()
    return catalog.model_copy(
        update={
            "runtime_capabilities": fixture_runtimes(),
            "model_routes": (*catalog.model_routes, openai_route),
        }
    )


def test_fixture_matches_the_default_capability_catalog() -> None:
    catalog = default_capability_catalog()
    expected = [
        item.model_dump(mode="json", by_alias=True) for item in catalog.runtime_capabilities
    ]
    assert load_fixture() == expected


def test_fixture_covers_every_installed_runtime() -> None:
    assert {item["runtime"] for item in load_fixture()} == set(INSTALLED_AGENT_RUNTIMES)


def _draft(runtime: str, route: ModelRouteCapability) -> AgentDraft:
    base = AgentDraft(
        draftId="draft_runtime_contract",
        tenantId="tenant-a",
        revision=1,
        spec=create_draft_spec(
            name="runtime-contract",
            domain="contract",
            display_name="运行时契约",
            description="验证 RuntimeCapabilities 契约的样例 Draft。",
            template=AgentTemplate.ANALYST,
        ),
        createdBy="builder-a",
        updatedBy="builder-a",
        createdAt=NOW,
        updatedAt=NOW,
    )
    return base.model_copy(
        update={
            "spec": base.spec.model_copy(
                update={
                    "runtime": runtime,
                    "model": base.spec.model.model_copy(
                        update={"route_id": route.route_id, "model": route.models[0]}
                    ),
                }
            )
        }
    )


def _routes_by_format() -> dict[str, ModelRouteCapability]:
    catalog = default_capability_catalog()
    routes: dict[str, ModelRouteCapability] = {}
    for route in catalog.model_routes:
        routes.setdefault(route.api_format, route)
    routes.setdefault(
        "openai_compatible",
        ModelRouteCapability(
            routeId="contract-openai-responses",
            label="Contract OpenAI Responses",
            provider="new-api",
            models=("contract-openai-model",),
            capabilities=("streaming", "tool_use"),
            apiFormat="openai_compatible",
            credentialReference="CONTRACT_OPENAI_KEY",
        ),
    )
    return routes


def _compiler() -> AgentDraftCompiler:
    return AgentDraftCompiler(fixture_catalog(_routes_by_format()["openai_compatible"]))


def _issue_codes(compiler: AgentDraftCompiler, draft: AgentDraft) -> set[str]:
    return {issue.code for issue in compiler.validate(draft).issues}


def test_compiler_conclusions_match_fixture_protocol_contract() -> None:
    compiler = _compiler()
    formats = _routes_by_format()
    anthropic_route = formats["anthropic_compatible"]
    openai_route = formats["openai_compatible"]

    fixture_by_runtime = {item.runtime: item for item in fixture_runtimes()}
    claude_formats = set(fixture_by_runtime["claude-agent-sdk"].model_api_formats)
    codex_formats = set(fixture_by_runtime["codex-app-server"].model_api_formats)
    assert anthropic_route.api_format in claude_formats
    assert anthropic_route.api_format not in codex_formats
    assert openai_route.api_format in codex_formats

    compatible_codex = _draft("codex-app-server", openai_route)
    assert "codex_responses_route_required" not in _issue_codes(compiler, compatible_codex)

    incompatible_codex = _draft("codex-app-server", anthropic_route)
    codes = _issue_codes(compiler, incompatible_codex)
    assert "codex_responses_route_required" in codes

    incompatible_claude = _draft("claude-agent-sdk", openai_route)
    if openai_route.api_format in claude_formats:
        assert "runtime_model_protocol_incompatible" not in _issue_codes(
            compiler, incompatible_claude
        )
    else:
        assert "runtime_model_protocol_incompatible" in _issue_codes(compiler, incompatible_claude)


def test_compiler_feature_gates_follow_fixture_capabilities() -> None:
    compiler = _compiler()
    openai_route = _routes_by_format()["openai_compatible"]
    anthropic_route = _routes_by_format()["anthropic_compatible"]

    codex_with_python_tool = _draft("codex-app-server", openai_route).model_copy(
        update={
            "spec": _draft("codex-app-server", openai_route).spec.model_copy(
                update={
                    "python_tools": (
                        DraftPythonTool(
                            name="contract_tool",
                            description="契约用例算子",
                            input_schema={"type": "object"},
                            code="def run():\n    return {}\n",
                        ),
                    ),
                }
            )
        }
    )
    assert "codex_python_tools_unsupported" in _issue_codes(compiler, codex_with_python_tool)

    claude_draft = _draft("claude-agent-sdk", anthropic_route)
    assert "runtime_python_tools_unsupported" not in _issue_codes(compiler, claude_draft)


def test_unregistered_runtime_is_rejected_by_the_compiler() -> None:
    """A runtime missing from the catalog (not installed) hits runtime_unknown."""

    compiler = AgentDraftCompiler(
        default_capability_catalog().model_copy(
            update={
                "runtime_capabilities": tuple(
                    item for item in fixture_runtimes() if item.runtime != "codex-app-server"
                ),
                "model_routes": (
                    *default_capability_catalog().model_routes,
                    _routes_by_format()["openai_compatible"],
                ),
            }
        )
    )
    ghost = _draft("codex-app-server", _routes_by_format()["openai_compatible"])
    issues = compiler.validate(ghost).issues
    runtime_unknown = [
        issue
        for issue in issues
        if issue.code == "runtime_unknown" and issue.severity is ValidationSeverity.ERROR
    ]
    assert runtime_unknown
