from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from harness.adapters.memory import (
    InMemoryArtifactStore,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryInputArtifactRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
)
from harness.application.events import EventService
from harness.application.input_artifacts import InputArtifactService
from harness.core.models import Run, RunStatus, Session
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.sandbox.local import LocalSandboxProvider
from harness.worker.orchestrator import RunOrchestrator

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def ids() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}_{counters[prefix]}"

    return generate


class InspectingRuntime:
    def __init__(
        self,
        *,
        emit_read_events: bool = False,
        persist_gate_request: bool = False,
        events: EventService | None = None,
    ) -> None:
        self.context: RuntimeContext | None = None
        self.content: bytes | None = None
        self.mode: int | None = None
        self.emit_read_events = emit_read_events
        self.persist_gate_request = persist_gate_request
        self.events = events

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        self.context = context
        staged = context.workspace / context.input_files[0]
        self.content = staged.read_bytes()
        self.mode = staged.stat().st_mode & 0o777
        yield RuntimeEvent(type="message.start")
        if self.emit_read_events:
            request_payload: dict[str, Any] = {
                "tool_call_id": "read-1",
                "name": "Read",
                "arguments": {"file_path": str(staged)},
            }
            if self.persist_gate_request:
                assert self.events is not None
                request_payload["arguments"] = {
                    "file_path": context.input_files[0]
                }
                request_payload["staged_input_read"] = True
                await self.events.append(
                    tenant_id=context.run.tenant_id,
                    run_id=context.run.run_id,
                    session_id=context.run.session_id,
                    event_type="tool.request",
                    payload=request_payload,
                )
            else:
                yield RuntimeEvent(type="tool.request", payload=request_payload)
            yield RuntimeEvent(
                type="tool.result",
                payload={
                    "tool_call_id": "read-1",
                    "content": self.content.decode(),
                    "is_error": False,
                },
            )
        yield RuntimeEvent(type="message.delta", payload={"text": "read"})
        yield RuntimeEvent(type="message.completed")


async def arrange(
    tmp_path: Path,
    *,
    session_user_id: str = "user-1",
    emit_read_events: bool = False,
    persist_gate_request: bool = False,
) -> tuple[
    RunOrchestrator,
    InspectingRuntime,
    InMemoryEventRepository,
    InputArtifactService,
]:
    generated_ids = ids()
    sessions = InMemorySessionRepository()
    runs = InMemoryRunRepository()
    events = InMemoryEventRepository()
    store = InMemoryArtifactStore()
    inputs = InputArtifactService(
        repository=InMemoryInputArtifactRepository(),
        store=store,
        id_generator=generated_ids,
        clock=lambda: NOW,
    )
    uploaded = await inputs.upload(
        tenant_id="tenant-a",
        user_id="user-1",
        name="../../private\n facts.txt",
        media_type="text/plain",
        content=b"The unique fact is amber-731.",
    )
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id=session_user_id,
        agent_name="file-agent",
        agent_version="1.0.0",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.QUEUED,
        idempotency_key="input-staging",
        created_at=NOW,
        updated_at=NOW,
        input={
            "prompt": "Read the attached file",
            "input_artifact_ids": [uploaded.input_artifact_id],
        },
    )
    await sessions.add(session)
    await runs.add(run)
    event_service = EventService(
        events,
        InMemoryEventBus(),
        clock=lambda: NOW,
        id_generator=generated_ids,
    )
    runtime = InspectingRuntime(
        emit_read_events=emit_read_events,
        persist_gate_request=persist_gate_request,
        events=event_service,
    )
    orchestrator = RunOrchestrator(
        sessions=sessions,
        runs=runs,
        events=event_service,
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: NOW,
        input_artifacts=inputs,
    )
    return orchestrator, runtime, events, inputs


@pytest.mark.asyncio
async def test_orchestrator_stages_safe_read_only_input_before_runtime(
    tmp_path: Path,
) -> None:
    orchestrator, runtime, repository, _inputs = await arrange(tmp_path)

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert runtime.content == b"The unique fact is amber-731."
    assert runtime.mode == 0o444
    assert runtime.context is not None
    assert len(runtime.context.input_files) == 1
    relative_path = runtime.context.input_files[0]
    assert relative_path.startswith("inputs/")
    assert ".." not in relative_path
    assert "\n" not in relative_path
    assert list(tmp_path.iterdir()) == []

    events = await repository.list_after("tenant-a", "run-1", 0)
    staged = next(event for event in events if event.type == "input.staged")
    assert staged.payload == {
        "input_artifact_id": "input_artifact_1",
        "name": "private_ facts.txt",
        "media_type": "text/plain",
        "size_bytes": 29,
        "path": relative_path,
    }
    assert "amber-731" not in repr(events)
    assert [event.type for event in events[:4]] == [
        "run.provisioning",
        "sandbox.provisioned",
        "input.staged",
        "run.running",
    ]


@pytest.mark.asyncio
async def test_orchestrator_redacts_staged_input_read_events(tmp_path: Path) -> None:
    orchestrator, runtime, repository, _inputs = await arrange(
        tmp_path,
        emit_read_events=True,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    assert runtime.context is not None
    events = await repository.list_after("tenant-a", "run-1", 0)
    request = next(event for event in events if event.type == "tool.request")
    tool_result = next(event for event in events if event.type == "tool.result")
    assert request.payload["arguments"]["file_path"] == runtime.context.input_files[0]
    assert tool_result.payload == {
        "tool_call_id": "read-1",
        "content": "[Input file content omitted from durable events]",
        "is_error": False,
        "redacted": True,
    }
    assert str(runtime.context.workspace) not in repr(events)
    assert "amber-731" not in repr(events)


@pytest.mark.asyncio
async def test_orchestrator_redacts_result_when_sdk_gate_persisted_request(
    tmp_path: Path,
) -> None:
    orchestrator, runtime, repository, _inputs = await arrange(
        tmp_path,
        emit_read_events=True,
        persist_gate_request=True,
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.SUCCEEDED
    events = await repository.list_after("tenant-a", "run-1", 0)
    request = next(event for event in events if event.type == "tool.request")
    tool_result = next(event for event in events if event.type == "tool.result")
    assert request.payload["staged_input_read"] is True
    assert tool_result.payload["redacted"] is True
    assert "amber-731" not in repr(events)
    assert runtime.context is not None
    assert str(runtime.context.workspace) not in repr(events)


@pytest.mark.asyncio
async def test_orchestrator_rejects_input_owned_by_another_user_before_runtime(
    tmp_path: Path,
) -> None:
    orchestrator, runtime, repository, _inputs = await arrange(
        tmp_path, session_user_id="user-2"
    )

    result = await orchestrator.execute("tenant-a", "run-1")

    assert result.status is RunStatus.FAILED
    assert runtime.context is None
    events = await repository.list_after("tenant-a", "run-1", 0)
    assert [event.type for event in events] == [
        "run.provisioning",
        "sandbox.provisioned",
        "run.failed",
    ]
