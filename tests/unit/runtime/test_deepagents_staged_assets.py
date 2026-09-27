from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("deepagents", reason="the DeepAgents kernel is an optional extra")

import harness.runtime.deepagents_runtime as deepagents_module  # noqa: E402
from harness.core.manifest import load_manifest, materialize_skill_snapshot_set  # noqa: E402
from harness.policy.rules import PolicyEngine, default_policy_rules  # noqa: E402
from harness.runtime.deepagents_plan import build_deepagents_plan  # noqa: E402
from harness.runtime.deepagents_runtime import (  # noqa: E402
    DeepagentsRuntime,
    DeepagentsRuntimeConfig,
)
from tests.unit.runtime.test_deepagents_runtime import _context  # noqa: E402


def test_deepagents_reuses_staged_skills_without_replacing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = load_manifest("agents/echo-agent/agent.yaml")
    runtime = DeepagentsRuntime(
        config=DeepagentsRuntimeConfig(
            snapshot=snapshot,
            route_id="default",
            provider="new-api",
            api_format="openai_compatible",
            model="gateway-model",
            base_url="https://gateway.example/v1",
            api_key=cast(Any, "secret"),
        ),
        approvals=cast(Any, None),
        events=cast(Any, None),
        policy=PolicyEngine(default_policy_rules()),
    )
    plan = build_deepagents_plan(
        builtin_tools=(),
        permission_policy=snapshot.manifest.spec.permissions.policy,
        model="gateway-model",
        with_skills=True,
    )
    monkeypatch.setattr(runtime, "_chat_model", lambda: cast(Any, object()))
    monkeypatch.setattr(runtime, "_bundle_tools", lambda _context: [])
    monkeypatch.setattr(runtime, "_platform_tools", lambda _context: [])
    monkeypatch.setattr(
        deepagents_module,
        "create_deep_agent",
        lambda **_kwargs: SimpleNamespace(with_config=lambda **_config: object()),
    )

    materialize_skill_snapshot_set((snapshot,), tmp_path)
    marker = tmp_path / ".claude/skills/workspace-validation/run-created.txt"
    marker.write_text("retained", encoding="utf-8")
    context = _context(tmp_path).model_copy(update={"agent_assets_staged": True})

    runtime._build_graph(context, plan=plan, backend=cast(Any, object()))

    assert marker.read_text(encoding="utf-8") == "retained"
