from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from harness.core.models import Run, RunStatus, Session
from harness.runtime.base import (
    RuntimeContext,
    RuntimeExecutionTimeoutError,
    RuntimeResultError,
)
from harness.runtime.codex_app_server import CodexAppServerOptions
from harness.runtime.codex_protocol import CodexMessage, CodexMessageKind
from harness.runtime.codex_runtime import (
    CodexAppServerRuntime,
    CodexProcess,
    CodexRpcConnection,
    CodexRuntimeConfig,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)


class FakeClient:
    def __init__(self, messages: list[CodexMessage], *, hang_turn: bool = False) -> None:
        self.messages = messages
        self.hang_turn = hang_turn
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.responses: list[tuple[int | str, object]] = []
        self.errors: list[tuple[int | str, int, str]] = []

    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        del timeout_seconds
        values = dict(params or {})
        self.requests.append((method, values))
        if method == "turn/start" and self.hang_turn:
            await asyncio.Future[None]()
        thread_id = str(values.get("threadId", "thread-1"))
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": thread_id}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        raise AssertionError(f"unexpected method: {method}")

    async def respond(self, request_id: int | str, result: object) -> None:
        self.responses.append((request_id, result))

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
    ) -> None:
        self.errors.append((request_id, code, message))

    async def inbound(self) -> AsyncIterator[CodexMessage]:
        for message in self.messages:
            yield message


class FakeProcess:
    def __init__(self, client: FakeClient) -> None:
        self.client: CodexRpcConnection | None = cast(CodexRpcConnection, client)
        self.started = False
        self.closed = False

    async def start(self) -> Mapping[str, object]:
        self.started = True
        return {}

    async def close(self) -> None:
        self.closed = True


def _context(tmp_path: Path, *, thread_id: str | None = None) -> RuntimeContext:
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        agent_version="1.0.0",
        runtime_type="codex-app-server",
        runtime_thread_id=thread_id,
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.RUNNING,
        idempotency_key="idem-1",
        input={"prompt": "build it"},
        created_at=NOW,
        updated_at=NOW,
    )
    return RuntimeContext(
        run=run,
        session=session,
        workspace=tmp_path,
        memory_projection="remember this",
    )


def _notification(method: str, params: dict[str, object] | None = None) -> CodexMessage:
    return CodexMessage(
        CodexMessageKind.NOTIFICATION,
        {"method": method, "params": params or {}},
    )


def _runtime(
    tmp_path: Path,
    client: FakeClient,
    *,
    timeout: float | None = None,
) -> tuple[CodexAppServerRuntime, FakeProcess, list[CodexAppServerOptions]]:
    process = FakeProcess(client)
    options_seen: list[CodexAppServerOptions] = []

    def factory(options: CodexAppServerOptions) -> CodexProcess:
        options_seen.append(options)
        return cast(CodexProcess, process)

    runtime = CodexAppServerRuntime(
        CodexRuntimeConfig(
            codex_path=tmp_path / "codex",
            model="gpt-test",
            developer_instructions="be precise",
            turn_timeout_seconds=timeout,
        ),
        process_factory=factory,
    )
    return runtime, process, options_seen


@pytest.mark.asyncio
async def test_starts_thread_turn_and_maps_stream(tmp_path: Path) -> None:
    client = FakeClient(
        [
            _notification("turn/started", {"turn": {"id": "turn-1"}}),
            _notification("item/agentMessage/delta", {"delta": "done"}),
            _notification("turn/completed", {"turn": {"status": "completed"}}),
        ]
    )
    runtime, process, options_seen = _runtime(tmp_path, client)

    events = [event async for event in runtime.execute(_context(tmp_path))]

    assert [event.type for event in events] == [
        "runtime.thread.started",
        "message.start",
        "message.delta",
        "message.completed",
        "runtime.turn.completed",
    ]
    assert events[0].payload["thread_id"] == "thread-1"
    assert events[2].payload["text"] == "done"
    assert client.requests[0] == (
        "thread/start",
        {
            "cwd": str(tmp_path),
            "approvalPolicy": "never",
            "sandbox": "workspace-write",
            "model": "gpt-test",
            "developerInstructions": "be precise",
        },
    )
    turn_params = client.requests[1][1]
    assert turn_params["threadId"] == "thread-1"
    assert turn_params["input"] == [
        {"type": "text", "text": "remember this\n\nbuild it"}
    ]
    assert turn_params["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(tmp_path)],
        "networkAccess": False,
    }
    assert options_seen[0].working_directory == tmp_path
    assert process.started is True
    assert process.closed is True


@pytest.mark.asyncio
async def test_resumes_existing_thread_and_declines_approval(tmp_path: Path) -> None:
    client = FakeClient(
        [
            CodexMessage(
                CodexMessageKind.SERVER_REQUEST,
                {
                    "id": 9,
                    "method": "item/commandExecution/requestApproval",
                    "params": {},
                },
            ),
            _notification("turn/completed", {"turn": {"status": "completed"}}),
        ]
    )
    runtime, process, _options = _runtime(tmp_path, client)

    _ = [
        event
        async for event in runtime.execute(_context(tmp_path, thread_id="thread-old"))
    ]

    assert client.requests[0][0] == "thread/resume"
    assert client.requests[0][1]["threadId"] == "thread-old"
    assert client.responses == [(9, {"decision": "decline"})]
    assert process.closed is True


@pytest.mark.asyncio
async def test_failed_turn_raises_runtime_result_error_and_closes(tmp_path: Path) -> None:
    client = FakeClient(
        [_notification("turn/completed", {"turn": {"status": "failed"}})]
    )
    runtime, process, _options = _runtime(tmp_path, client)

    with pytest.raises(RuntimeResultError, match="codex_turn_failed"):
        _ = [event async for event in runtime.execute(_context(tmp_path))]

    assert process.closed is True


@pytest.mark.asyncio
async def test_turn_timeout_closes_process(tmp_path: Path) -> None:
    client = FakeClient([], hang_turn=True)
    runtime, process, _options = _runtime(tmp_path, client, timeout=0.01)

    with pytest.raises(RuntimeExecutionTimeoutError, match="timed out"):
        _ = [event async for event in runtime.execute(_context(tmp_path))]

    assert process.closed is True
