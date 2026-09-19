"""What the DeepAgents runtime forwards, and what it deliberately withholds.

The runtime sits between the graph and the Worker's generic passes. Two facts
about that boundary are load-bearing:

* ``tool.request`` is written by ``DeepagentsToolGate``, which names the tool in
  the platform vocabulary and redacts the arguments against that name. The
  mapper also produces one, named the way the runtime spells it. Forwarding the
  second copy makes the Worker's generic policy pass re-decide a call the gate
  already decided -- and record the unredacted arguments while doing it -- so the
  runtime withholds it, exactly as ``ClaudeSdkRuntime`` does.
* Withholding it must not blind the Manifest's tool-call limit, which is counted
  before the event is dropped.

Driving ``execute`` needs a compiled graph, so ``_build_graph`` is replaced: the
graph is not what is under test here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("deepagents", reason="the DeepAgents kernel is an optional extra")

from harness.core.manifest import AgentManifestSnapshot  # noqa: E402
from harness.core.models import Run, RunStatus, Session  # noqa: E402
from harness.policy.rules import PolicyEngine, default_policy_rules  # noqa: E402
from harness.runtime.base import (  # noqa: E402
    RuntimeContext,
    RuntimeEvent,
    RuntimeResultError,
)
from harness.runtime.deepagents_events import DeepagentsStreamMapper  # noqa: E402
from harness.runtime.deepagents_runtime import (  # noqa: E402
    DeepagentsRuntime,
    DeepagentsRuntimeConfig,
)

NOW = datetime(2026, 9, 19, tzinfo=UTC)


def _snapshot(*, max_tool_calls: int | None = None) -> AgentManifestSnapshot:
    limits: dict[str, object] = {}
    if max_tool_calls is not None:
        limits["maxToolCalls"] = max_tool_calls
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
                    "tools": [{"builtin": "Read"}],
                    "permissions": {"policy": "default"},
                    "limits": limits,
                },
            },
            "system_prompt": "You are a test.",
            "python_tool_snapshots": [],
            "content_hash": "a" * 64,
        }
    )


def _config(*, max_tool_calls: int | None = None) -> DeepagentsRuntimeConfig:
    return DeepagentsRuntimeConfig(
        snapshot=_snapshot(max_tool_calls=max_tool_calls),
        route_id="route-1",
        provider="new-api",
        api_format="openai_compatible",
        model="gateway-model",
        base_url="https://gateway.example",
        api_key=cast(Any, "sk-test"),
    )


def _context(workspace: Path) -> RuntimeContext:
    return RuntimeContext(
        run=Run(
            run_id="run-1",
            session_id="session-1",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="deepagents-runtime",
            input={"prompt": "写一个文件"},
            created_at=NOW,
            updated_at=NOW,
        ),
        session=Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-a",
            agent_name="score-agent",
            agent_version="1.0.0",
            created_at=NOW,
        ),
        workspace=workspace,
        sandbox_command_executor=cast(Any, _unused_executor),
    )


async def _unused_executor(command: str, **kwargs: Any) -> Any:
    raise AssertionError(f"the sandbox must not be reached in this test: {command}")


class _FakeGraph:
    """Replay prepared ``(stream_mode, payload)`` batches instead of running."""

    def __init__(self, batches: list[tuple[str, object]]) -> None:
        self._batches = batches

    async def astream(
        self,
        payload: object,
        *,
        config: Mapping[str, object],
        stream_mode: object,
    ) -> AsyncIterator[tuple[str, object]]:
        for batch in self._batches:
            yield batch


def _tool_call_batch(tool_call_id: str, name: str, arguments: dict[str, Any]) -> tuple[str, object]:
    """One `updates` batch carrying a single model tool call."""

    message = SimpleNamespace(
        tool_calls=[{"id": tool_call_id, "name": name, "args": arguments}],
        tool_call_id=None,
        content="",
        status=None,
    )
    return ("updates", {"model": {"messages": [message]}})


def _runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    batches: list[tuple[str, object]],
    max_tool_calls: int | None = None,
) -> DeepagentsRuntime:
    graph = _FakeGraph(batches)
    monkeypatch.setattr(
        DeepagentsRuntime,
        "_build_graph",
        lambda self, context, *, plan, backend: graph,
    )
    return DeepagentsRuntime(
        config=_config(max_tool_calls=max_tool_calls),
        approvals=cast(Any, None),
        events=cast(Any, None),
        policy=PolicyEngine(default_policy_rules()),
    )


async def _drain(runtime: DeepagentsRuntime, context: RuntimeContext) -> list[RuntimeEvent]:
    return [event async for event in runtime.execute(context)]


@pytest.mark.asyncio
async def test_the_runtime_withholds_the_mappers_tool_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate owns this fact; a second copy would be re-decided by the Worker."""

    batch = _tool_call_batch("call-1", "write_file", {"file_path": "a.txt"})
    # Non-vacuity guard: the mapper really does produce one for this batch, so
    # the runtime is what withholds it. Were the mapper to stop emitting it, the
    # assertion below would pass for the wrong reason.
    mapped = DeepagentsStreamMapper(model="gateway-model", provider="new-api").updates(
        cast(Mapping[str, Any], batch[1])
    )
    assert [event.type for event in mapped] == ["tool.request"]

    runtime = _runtime(
        monkeypatch,
        batches=[batch],
    )

    events = await _drain(runtime, _context(tmp_path))

    assert [event.type for event in events if event.type == "tool.request"] == []
    # The mapped stream still reaches the Worker for everything else.
    assert "model.route.selected" in {event.type for event in events}
    assert "runtime.result" in {event.type for event in events}


@pytest.mark.asyncio
async def test_a_withheld_request_still_counts_towards_the_manifest_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Withholding must not blind the tool-call ceiling."""

    runtime = _runtime(
        monkeypatch,
        batches=[
            _tool_call_batch("call-1", "write_file", {"file_path": "a.txt"}),
            _tool_call_batch("call-2", "read_file", {"file_path": "a.txt"}),
        ],
        max_tool_calls=1,
    )

    with pytest.raises(RuntimeResultError) as raised:
        await _drain(runtime, _context(tmp_path))

    assert raised.value.error_code == "deepagents_tool_call_limit"


@pytest.mark.asyncio
async def test_a_run_within_the_limit_completes_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(
        monkeypatch,
        batches=[_tool_call_batch("call-1", "write_file", {"file_path": "a.txt"})],
        max_tool_calls=4,
    )

    events = await _drain(runtime, _context(tmp_path))

    assert events[-1].type == "runtime.result"
    assert events[-1].payload["subtype"] == "success"
