from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from harness.config import Settings
from harness.core.manifest import SubagentSpec, load_manifest
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.observability.provider import build_observability
from harness.runtime.base import RuntimeEvent
from harness.runtime.subagent_governance import (
    SUBAGENT_EVENT_SCHEMA,
    SubagentGovernanceError,
    SubagentRuntimeGovernor,
)


def _version(name: str = "helper", version: str = "1.0.0") -> AgentVersion:
    snapshot = load_manifest("tests/fixtures/agents/helper-agent/agent.yaml")
    return AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name=name,
        version=version,
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )


def _root(*, concurrency: int = 2, tokens: int = 100):
    snapshot = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    bindings = (
        SubagentSpec(ref="helper@1.0.0", alias="fact-checker"),
        SubagentSpec(ref="helper@1.0.0", alias="risk-reviewer"),
    )
    limits = snapshot.manifest.spec.limits.model_copy(
        update={
            "max_concurrent_subagents": concurrency,
            "max_subagent_usage_units": tokens,
        }
    )
    spec = snapshot.manifest.spec.model_copy(update={"subagents": bindings, "limits": limits})
    manifest = snapshot.manifest.model_copy(update={"spec": spec})
    return snapshot.model_copy(update={"manifest": manifest})


def _started(task_id: str, alias: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="subagent.started",
        payload={
            "task_id": task_id,
            "task_type": alias,
            "parent_tool_use_id": f"call-{task_id}",
            "description": "private delegated request",
        },
    )


def test_same_version_multiple_aliases_keep_distinct_runtime_identity() -> None:
    version = _version()
    governor = SubagentRuntimeGovernor(
        root=_root(),
        subagent_versions={"fact-checker": version, "risk-reviewer": version},
    )

    first = governor.process(_started("one", "fact-checker"), run_id="run-1")[0]
    second = governor.process(_started("two", "risk-reviewer"), run_id="run-1")[0]
    terminal = governor.process(
        RuntimeEvent(
            type="subagent.completed",
            payload={
                "task_id": "one",
                "status": "completed",
                "usage": {"total_tokens": 20, "duration_ms": 30},
                "summary": "private child output",
            },
        ),
        run_id="run-1",
    )[0]

    assert first.payload["alias"] == "fact-checker"
    assert second.payload["alias"] == "risk-reviewer"
    assert terminal.payload["agent_version"] == "1.0.0"
    assert terminal.payload["policy_profile"] == "local-standard"
    assert terminal.payload["event_schema"] == SUBAGENT_EVENT_SCHEMA
    assert terminal.payload["depth"] == 1


def test_ordinary_background_tasks_bypass_subagent_governance() -> None:
    governor = SubagentRuntimeGovernor(root=_root(), subagent_versions={})
    events = [
        RuntimeEvent(
            type="runtime.task.started",
            payload={
                "task_id": "bash-1",
                "task_type": "local_bash",
                "parent_tool_use_id": "bash-tool-1",
            },
        ),
        RuntimeEvent(
            type="runtime.task.updated",
            payload={"task_id": "bash-1", "status": "running"},
        ),
        RuntimeEvent(
            type="runtime.task.completed",
            payload={"task_id": "bash-1", "status": "completed"},
        ),
    ]

    governed = [governor.process(event, run_id="run-1")[0] for event in events]

    assert [event.type for event in governed] == [
        "runtime.task.started",
        "runtime.task.updated",
        "runtime.task.completed",
    ]
    assert governor.active_tasks == ()


def test_runtime_task_linked_to_agent_tool_is_promoted_to_subagent() -> None:
    version = _version()
    governor = SubagentRuntimeGovernor(
        root=_root(),
        subagent_versions={"fact-checker": version, "risk-reviewer": version},
    )
    governor.process(
        RuntimeEvent(
            type="tool.request",
            payload={
                "tool_call_id": "agent-tool-1",
                "name": "Task",
                "arguments": {"subagent_type": "fact-checker"},
            },
        ),
        run_id="run-1",
    )

    started = governor.process(
        RuntimeEvent(
            type="runtime.task.started",
            payload={
                "task_id": "child-1",
                "task_type": "fact-checker",
                "parent_tool_use_id": "agent-tool-1",
            },
        ),
        run_id="run-1",
    )[0]
    completed = governor.process(
        RuntimeEvent(
            type="runtime.task.completed",
            payload={"task_id": "child-1", "status": "completed"},
        ),
        run_id="run-1",
    )[0]

    assert started.type == "subagent.started"
    assert started.payload["alias"] == "fact-checker"
    assert completed.type == "subagent.completed"
    assert completed.payload["alias"] == "fact-checker"


def test_runtime_task_for_undeclared_agent_tool_still_fails_closed() -> None:
    version = _version()
    governor = SubagentRuntimeGovernor(
        root=_root(),
        subagent_versions={"fact-checker": version, "risk-reviewer": version},
    )
    governor.process(
        RuntimeEvent(
            type="tool.request",
            payload={
                "tool_call_id": "agent-tool-1",
                "name": "Agent",
                "arguments": {"name": "writer"},
            },
        ),
        run_id="run-1",
    )

    with pytest.raises(SubagentGovernanceError, match="undeclared"):
        governor.process(
            RuntimeEvent(
                type="runtime.task.started",
                payload={
                    "task_id": "child-1",
                    "parent_tool_use_id": "agent-tool-1",
                },
            ),
            run_id="run-1",
        )


def test_unbound_alias_and_concurrency_fail_closed() -> None:
    version = _version()
    governor = SubagentRuntimeGovernor(
        root=_root(concurrency=1),
        subagent_versions={"fact-checker": version, "risk-reviewer": version},
    )

    with pytest.raises(SubagentGovernanceError, match="undeclared"):
        governor.process(_started("unknown", "writer"), run_id="run-1")

    governor.process(_started("one", "fact-checker"), run_id="run-1")
    with pytest.raises(SubagentGovernanceError, match="concurrent"):
        governor.process(_started("two", "risk-reviewer"), run_id="run-1")


def test_subagent_usage_is_observational_and_missing_terminal_is_explicit() -> None:
    version = _version()
    governor = SubagentRuntimeGovernor(
        root=_root(tokens=10),
        subagent_versions={"fact-checker": version, "risk-reviewer": version},
    )
    governor.process(_started("one", "fact-checker"), run_id="run-1")

    updated = governor.process(
        RuntimeEvent(
            type="subagent.updated",
            payload={"task_id": "one", "usage": {"total_tokens": 1_000_000}},
        ),
        run_id="run-1",
    )
    assert updated[0].payload["usage"]["total_tokens"] == 1_000_000

    failed = governor.fail_unfinished(reason="stream_closed", run_id="run-1")
    assert [(event.type, event.payload["error_code"]) for event in failed] == [
        ("subagent.failed", "stream_closed")
    ]


def test_subagent_span_contains_identity_and_usage_but_no_child_content() -> None:
    exporter = InMemorySpanExporter()
    observability = build_observability(
        Settings(otel_enabled=True, otlp_endpoint="http://unused/v1/traces"),
        exporter=exporter,
        processor_factory=SimpleSpanProcessor,
    )
    version = _version()
    governor = SubagentRuntimeGovernor(
        root=_root(),
        subagent_versions={"fact-checker": version, "risk-reviewer": version},
        observability=observability,
    )
    governor.process(_started("one", "fact-checker"), run_id="run-1")
    governor.process(
        RuntimeEvent(
            type="subagent.completed",
            payload={
                "task_id": "one",
                "status": "completed",
                "summary": "PRIVATE CHILD BODY",
                "usage": {"total_tokens": 9, "tool_uses": 2, "duration_ms": 40},
            },
        ),
        run_id="run-1",
    )

    span = next(
        item for item in exporter.get_finished_spans() if item.name == "harness.subagent.run"
    )
    assert span.attributes is not None
    assert span.attributes["harness.subagent.alias"] == "fact-checker"
    assert span.attributes["harness.subagent.agent_version"] == "1.0.0"
    assert span.attributes["harness.subagent.usage.total_units"] == 9
    assert "PRIVATE CHILD BODY" not in repr(span.attributes)
