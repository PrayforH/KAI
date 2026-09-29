import json
from pathlib import Path

import pytest
import yaml

from harness.core.manifest import (
    ManifestValidationError,
    ToolDirectoryEntry,
    ToolDirectorySnapshot,
    load_manifest,
    materialize_python_tool_snapshot_set,
)

FIXTURE = Path("tests/fixtures/agents/echo-agent/agent.yaml")
VALIDATION_AGENT = Path("agents/echo-agent/agent.yaml")
HELPER_AGENT = Path("agents/helper-agent/agent.yaml")


def test_loads_valid_manifest_and_resolves_prompt() -> None:
    snapshot = load_manifest(FIXTURE)

    assert snapshot.manifest.metadata.name == "echo-agent"
    assert snapshot.manifest.metadata.version == "0.1.0"
    assert snapshot.system_prompt.startswith("You are a deterministic echo agent")
    assert len(snapshot.content_hash) == 64


def test_content_hash_is_deterministic() -> None:
    assert load_manifest(FIXTURE).content_hash == load_manifest(FIXTURE).content_hash


def test_snapshots_and_materializes_self_contained_bundle_python_tool(
    tmp_path: Path,
) -> None:
    root = tmp_path / "agent"
    root.mkdir()
    manifest = yaml.safe_load(FIXTURE.read_text())
    manifest["spec"]["tools"].append({"python": "bundle:tools/normalize.py"})
    (root / "agent.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    (root / "prompts").mkdir()
    (root / "prompts/system.md").write_text(
        (FIXTURE.parent / "prompts/system.md").read_text()
    )
    (root / "tools").mkdir()
    source = (
        "TOOL_SPEC = {'name': 'normalize', 'description': 'Normalize input.', "
        "'input_schema': {'type': 'object', 'properties': {'value': {'type': 'number'}}}}\n"
        "def run(arguments):\n    return {'value': arguments['value']}\n"
    )
    (root / "tools/normalize.py").write_text(source)

    snapshot = load_manifest(root / "agent.yaml")
    materialized = materialize_python_tool_snapshot_set(
        (snapshot,), tmp_path / "workspace"
    )

    assert snapshot.python_tool_snapshots[0].name == "normalize"
    relative = materialized[snapshot.content_hash]["bundle:tools/normalize.py"]
    assert (tmp_path / "workspace" / relative).read_text() == source


def test_validation_agent_exposes_workspace_tools_with_safe_prompt() -> None:
    snapshot = load_manifest(VALIDATION_AGENT)

    assert snapshot.manifest.metadata.version == "0.4.1"
    assert tuple(tool.builtin for tool in snapshot.manifest.spec.tools) == (
        "Read",
        "Glob",
        "Grep",
        "Write",
        "Edit",
        "Bash",
        "Task",
    )
    assert all(tool.mcp is None for tool in snapshot.manifest.spec.tools)
    assert tuple(item.ref for item in snapshot.manifest.spec.subagents) == (
        "helper-agent@1.0.0",
    )
    assert "only when the user requests" in snapshot.system_prompt.lower()
    assert "run workspace" in snapshot.system_prompt.lower()
    assert "untrusted" in snapshot.system_prompt.lower()
    assert "echo the user's request" not in snapshot.system_prompt.lower()


def test_validation_helper_agent_is_bounded_to_read_only_investigation() -> None:
    snapshot = load_manifest(HELPER_AGENT)

    assert snapshot.manifest.metadata.name == "helper-agent"
    assert snapshot.manifest.metadata.version == "1.0.0"
    assert tuple(tool.builtin for tool in snapshot.manifest.spec.tools) == (
        "Read",
        "Glob",
        "Grep",
    )
    assert snapshot.manifest.spec.subagents == ()
    assert "parent agent" in snapshot.system_prompt.lower()


def test_rejects_unknown_runtime(tmp_path: Path) -> None:
    manifest = (FIXTURE.read_text()).replace("claude-agent-sdk", "unknown-runtime")
    path = tmp_path / "agent.yaml"
    path.write_text(manifest)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    with pytest.raises(ManifestValidationError, match="runtime"):
        load_manifest(path)


def test_rejects_latest_subagent_in_production(tmp_path: Path) -> None:
    manifest = FIXTURE.read_text().replace("helper@1.0.0", "helper@latest")
    path = tmp_path / "agent.yaml"
    path.write_text(manifest)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    with pytest.raises(ManifestValidationError, match="latest"):
        load_manifest(path, environment="production")


def test_supports_multiple_role_aliases_for_one_pinned_subagent(tmp_path: Path) -> None:
    manifest = yaml.safe_load(FIXTURE.read_text())
    manifest["spec"]["subagents"] = [
        {
            "ref": "helper@1.0.0",
            "alias": "fact-checker",
            "description": "Verify facts and return source-backed findings.",
            "background": True,
        },
        {
            "ref": "helper@1.0.0",
            "alias": "risk-reviewer",
            "description": "Challenge conclusions and identify uncertainty.",
        },
    ]
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    snapshot = load_manifest(path, environment="production")

    fact_checker, risk_reviewer = snapshot.manifest.spec.subagents
    assert fact_checker.runtime_name == "fact-checker"
    assert fact_checker.background is True
    assert risk_reviewer.runtime_name == "risk-reviewer"
    assert risk_reviewer.background is False


def test_rejects_duplicate_subagent_role_aliases(tmp_path: Path) -> None:
    manifest = yaml.safe_load(FIXTURE.read_text())
    manifest["spec"]["subagents"] = [
        {"ref": "helper@1.0.0", "alias": "reviewer"},
        {"ref": "helper@1.0.0", "alias": "reviewer"},
    ]
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    with pytest.raises(ManifestValidationError, match="duplicate subagent runtime name"):
        load_manifest(path, environment="production")


def test_rejects_subagent_count_and_depth_beyond_one_level(tmp_path: Path) -> None:
    manifest = yaml.safe_load(FIXTURE.read_text())
    manifest["spec"]["limits"]["maxSubagents"] = 1
    manifest["spec"]["subagents"] = [
        {"ref": "helper@1.0.0", "alias": "one"},
        {"ref": "helper@1.0.0", "alias": "two"},
    ]
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    with pytest.raises(ManifestValidationError, match="maxSubagents"):
        load_manifest(path, environment="production")

    manifest["spec"]["limits"]["maxSubagents"] = 2
    manifest["spec"]["limits"]["maxSubagentDepth"] = 2
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    with pytest.raises(ManifestValidationError, match="maxSubagentDepth"):
        load_manifest(path, environment="production")


def test_rejects_missing_prompt(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(FIXTURE.read_text())

    with pytest.raises(ManifestValidationError, match="system prompt"):
        load_manifest(path)


def test_rejects_inline_secrets(tmp_path: Path) -> None:
    manifest = FIXTURE.read_text() + "\n  apiKey: sk-should-not-be-here\n"
    path = tmp_path / "agent.yaml"
    path.write_text(manifest)
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    with pytest.raises(ManifestValidationError, match="secret-like field"):
        load_manifest(path)


@pytest.mark.parametrize("runtime,context", [
    ("deepagents", {"autoCompactTokenLimit": 2048, "keepRecentMessages": 2}),
    ("codex-app-server", {"autoCompactTokenLimit": 64000, "contextWindowTokens": 128000}),
])
def test_context_token_budgets_pass_publication_secret_check(
    tmp_path: Path, runtime: str, context: dict[str, int],
) -> None:
    manifest = yaml.safe_load(FIXTURE.read_text())
    manifest["spec"].update(runtime=runtime, context=context)
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")
    snapshot = load_manifest(path)
    assert (
        snapshot.manifest.spec.context.auto_compact_token_limit == context["autoCompactTokenLimit"]
    )

    manifest["spec"]["context"]["autoCompactTokenLimit"] = "sk-not-a-budget"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    with pytest.raises(ManifestValidationError, match="secret-like field"):
        load_manifest(path)


def test_on_demand_manifest_requires_a_hash_validated_tool_directory(
    tmp_path: Path,
) -> None:
    manifest = yaml.safe_load(FIXTURE.read_text())
    manifest["spec"]["toolExposureMode"] = "on_demand"
    manifest["spec"]["model"]["requiredCapabilities"].append("tool_search")
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("prompt")

    with pytest.raises(ManifestValidationError, match="tool-directory.json"):
        load_manifest(path)

    directory = ToolDirectorySnapshot.create(
        catalog_revision=7,
        exposure_mode="on_demand",
        entries=(
            ToolDirectoryEntry(
                name="Read",
                source="builtin",
                logicalReference="Read",
                description="Read a file in the isolated workspace.",
                risk="low",
                resultTrust="safe",
            ),
            ToolDirectoryEntry(
                name="Task",
                source="builtin",
                logicalReference="Task",
                description="Delegate to a pinned Sub Agent.",
                risk="medium",
                resultTrust="safe",
            ),
        ),
    )
    directory_path = tmp_path / "tool-directory.json"
    directory_path.write_text(
        json.dumps(directory.model_dump(mode="json", by_alias=True))
    )

    snapshot = load_manifest(path)

    assert snapshot.tool_directory == directory
    assert snapshot.manifest.spec.tool_exposure_mode == "on_demand"

    tampered = directory.model_dump(mode="json", by_alias=True)
    tampered["entries"][0]["description"] = "tampered"
    directory_path.write_text(json.dumps(tampered))
    with pytest.raises(ManifestValidationError, match="content hash"):
        load_manifest(path)
