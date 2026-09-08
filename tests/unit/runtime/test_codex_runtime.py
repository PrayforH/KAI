from __future__ import annotations

import asyncio
import json
import shutil
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
from harness.runtime.codex_app_server import CodexAppServerOptions, CodexRpcRemoteError
from harness.runtime.codex_protocol import CodexMessage, CodexMessageKind
from harness.runtime.codex_runtime import (
    CodexAppServerRuntime,
    CodexProcess,
    CodexRpcConnection,
    CodexRuntimeConfig,
    _persist_local_codex_home,
    _prepare_local_codex_home,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)


class FakeClient:
    def __init__(
        self,
        messages: list[CodexMessage],
        *,
        hang_turn: bool = False,
        fail_start: bool = False,
        fail_resume: bool = False,
        start_error_message: str = "required MCP server failed to initialize",
        resume_error_message: str = "thread metadata is unavailable",
    ) -> None:
        self.messages = messages
        self.hang_turn = hang_turn
        self.fail_start = fail_start
        self.fail_resume = fail_resume
        self.start_error_message = start_error_message
        self.resume_error_message = resume_error_message
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
        if method == "thread/resume" and self.fail_resume:
            raise CodexRpcRemoteError(
                method=method,
                code=-32602,
                message=self.resume_error_message,
            )
        if method == "thread/start" and self.fail_start:
            raise CodexRpcRemoteError(
                method=method,
                code=-32603,
                message=self.start_error_message,
            )
        if method == "turn/start" and self.hang_turn:
            await asyncio.Future[None]()
        thread_id = str(values.get("threadId", "thread-1"))
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": thread_id}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/interrupt":
            return {}
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


def _context(
    tmp_path: Path,
    *,
    thread_id: str | None = None,
    context_projection: str = "",
    conversation_prompts: list[str] | None = None,
) -> RuntimeContext:
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
        input={
            "prompt": "build it",
            **(
                {"conversation_prompts": conversation_prompts}
                if conversation_prompts is not None
                else {}
            ),
        },
        created_at=NOW,
        updated_at=NOW,
    )
    return RuntimeContext(
        run=run,
        session=session,
        workspace=tmp_path,
        memory_projection="remember this",
        context_projection=context_projection,
    )


def _notification(method: str, params: dict[str, object] | None = None) -> CodexMessage:
    return CodexMessage(
        CodexMessageKind.NOTIFICATION,
        {"method": method, "params": params or {}},
    )


def test_local_codex_home_is_stable_across_restored_run_workspaces(
    tmp_path: Path,
) -> None:
    first_workspace = tmp_path / "run_first-random"
    first_workspace.mkdir()
    first = _prepare_local_codex_home(first_workspace, _context(first_workspace))
    rollout = first.native / ".codex" / "sessions" / "rollout-thread-1.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    stable_home = first.native
    _persist_local_codex_home(first)

    second_workspace = tmp_path / "run_second-random"
    second_workspace.mkdir()
    shutil.copytree(
        first_workspace / ".codex-home",
        second_workspace / ".codex-home",
    )
    second = _prepare_local_codex_home(
        second_workspace,
        _context(second_workspace, thread_id="thread-1"),
    )

    assert second.native == stable_home
    assert (second.native / ".codex" / "sessions" / rollout.name).is_file()
    _persist_local_codex_home(second)
    assert (second_workspace / ".codex-home" / ".harness-native-home").read_text() == str(
        stable_home
    )


def test_local_codex_home_recovers_legacy_absolute_rollout_path(tmp_path: Path) -> None:
    old_workspace = tmp_path / "run_old-random"
    current_workspace = tmp_path / "run_current-random"
    current_workspace.mkdir()
    archived = current_workspace / ".codex-home"
    rollout = archived / ".codex" / "sessions" / "2026" / "08" / "23" / "rollout-thread-old.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "thread-old", "cwd": str(old_workspace)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    home = _prepare_local_codex_home(
        current_workspace,
        _context(current_workspace, thread_id="thread-old"),
    )

    assert home.native == old_workspace / ".codex-home"
    assert rollout.name in {item.name for item in home.native.rglob("rollout-thread-old.jsonl")}
    _persist_local_codex_home(home)


def _runtime(
    tmp_path: Path,
    client: FakeClient,
    *,
    timeout: float | None = None,
    max_tool_calls: int | None = None,
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
            config_overrides=('model_provider="test"',),
            turn_timeout_seconds=timeout,
            max_tool_calls=max_tool_calls,
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
    assert turn_params["input"] == [{"type": "text", "text": "remember this\n\nbuild it"}]
    assert turn_params["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(tmp_path)],
        "networkAccess": False,
    }
    assert options_seen[0].working_directory == tmp_path
    assert options_seen[0].config_overrides == ('model_provider="test"',)
    assert process.started is True
    assert process.closed is True


@pytest.mark.asyncio
async def test_child_thread_completion_does_not_finish_parent_turn(tmp_path: Path) -> None:
    client = FakeClient(
        [
            _notification("turn/started", {"threadId": "child-1"}),
            _notification(
                "item/agentMessage/delta",
                {"threadId": "child-1", "delta": "child result"},
            ),
            _notification(
                "turn/completed",
                {"threadId": "child-1", "turn": {"status": "completed"}},
            ),
            _notification(
                "item/agentMessage/delta",
                {"threadId": "thread-1", "delta": "lead result"},
            ),
            _notification(
                "turn/completed",
                {"threadId": "thread-1", "turn": {"status": "completed"}},
            ),
        ]
    )
    runtime, _process, _options = _runtime(tmp_path, client)

    events = [event async for event in runtime.execute(_context(tmp_path))]

    assert [event.type for event in events] == [
        "runtime.thread.started",
        "subagent.started",
        "subagent.delta",
        "subagent.completed",
        "message.delta",
        "message.completed",
        "runtime.turn.completed",
    ]
    assert events[4].payload["text"] == "lead result"


@pytest.mark.asyncio
async def test_remote_transport_uses_remote_workspace_for_codex(tmp_path: Path) -> None:
    client = FakeClient([_notification("turn/completed", {"turn": {"status": "completed"}})])
    runtime, remote_process, local_options = _runtime(tmp_path, client)
    remote_options: list[CodexAppServerOptions] = []

    def transport_factory(options: object) -> object:
        remote_options.append(cast(CodexAppServerOptions, options))
        return remote_process

    context = _context(tmp_path).model_copy(
        update={
            "remote_workspace": "/home/daytona/harness/run-1",
            "runtime_transport_factory": transport_factory,
        }
    )

    _ = [event async for event in runtime.execute(context)]

    assert local_options == []
    assert remote_options[0].working_directory == Path("/home/daytona/harness/run-1")
    assert client.requests[0][1]["cwd"] == "/home/daytona/harness/run-1"
    assert client.requests[1][1]["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": ["/home/daytona/harness/run-1"],
        "networkAccess": False,
    }


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

    _ = [event async for event in runtime.execute(_context(tmp_path, thread_id="thread-old"))]

    assert client.requests[0][0] == "thread/resume"
    assert client.requests[0][1]["threadId"] == "thread-old"
    assert client.responses == [(9, {"decision": "decline"})]
    assert process.closed is True


@pytest.mark.asyncio
async def test_stale_thread_recovers_with_fresh_thread_and_context(tmp_path: Path) -> None:
    client = FakeClient(
        [_notification("turn/completed", {"turn": {"status": "completed"}})],
        fail_resume=True,
    )
    runtime, process, _options = _runtime(tmp_path, client)

    events = [
        event
        async for event in runtime.execute(
            _context(
                tmp_path,
                thread_id="thread-old",
                context_projection="prior conversation digest",
            )
        )
    ]

    assert [event.type for event in events[:2]] == [
        "runtime.session.recovered",
        "runtime.thread.started",
    ]
    assert events[0].payload == {
        "previous_session_id": "thread-old",
        "runtime": "codex-app-server",
    }
    assert [method for method, _params in client.requests[:3]] == [
        "thread/resume",
        "thread/start",
        "turn/start",
    ]
    assert client.requests[2][1]["input"] == [
        {
            "type": "text",
            "text": "remember this\n\nprior conversation digest\n\nbuild it",
        }
    ]
    assert process.closed is True


@pytest.mark.asyncio
async def test_stale_thread_recovery_includes_durable_user_turns(tmp_path: Path) -> None:
    client = FakeClient(
        [_notification("turn/completed", {"turn": {"status": "completed"}})],
        fail_resume=True,
    )
    runtime, _process, _options = _runtime(tmp_path, client)

    _ = [
        event
        async for event in runtime.execute(
            _context(
                tmp_path,
                thread_id="thread-old",
                conversation_prompts=["collect all pages", "build it"],
            )
        )
    ]

    prompt = client.requests[2][1]["input"]
    assert isinstance(prompt, list)
    assert "collect all pages" in str(prompt[0])
    assert str(prompt[0]).count("build it") == 1


@pytest.mark.asyncio
async def test_non_stale_resume_error_does_not_silently_start_new_thread(tmp_path: Path) -> None:
    client = FakeClient(
        [],
        fail_resume=True,
        resume_error_message="model provider rate limit",
    )
    runtime, process, _options = _runtime(tmp_path, client)

    with pytest.raises(RuntimeResultError, match="codex_thread_resume_failed") as raised:
        _ = [event async for event in runtime.execute(_context(tmp_path, thread_id="thread-old"))]

    assert raised.value.error_code == "codex_thread_resume_failed"
    assert raised.value.user_message == "Codex 会话恢复失败，请新建对话后重试。"
    assert [method for method, _params in client.requests] == ["thread/resume"]
    assert process.closed is True


@pytest.mark.asyncio
async def test_thread_start_error_explains_required_mcp_failure(tmp_path: Path) -> None:
    client = FakeClient([], fail_start=True)
    runtime, process, _options = _runtime(tmp_path, client)

    with pytest.raises(RuntimeResultError, match="codex_thread_start_failed") as raised:
        _ = [event async for event in runtime.execute(_context(tmp_path))]

    assert raised.value.error_code == "codex_thread_start_failed"
    assert raised.value.user_message is not None
    assert "MCP" in raised.value.user_message
    assert [method for method, _params in client.requests] == ["thread/start"]
    assert process.closed is True


@pytest.mark.asyncio
async def test_failed_turn_raises_runtime_result_error_and_closes(tmp_path: Path) -> None:
    client = FakeClient(
        [
            _notification(
                "error",
                {
                    "error": {
                        "message": "context window exceeded",
                        "codexErrorInfo": {"Other": "too many tokens"},
                    }
                },
            ),
            _notification("turn/completed", {"turn": {"status": "failed"}}),
        ]
    )
    runtime, process, _options = _runtime(tmp_path, client)
    events = []

    with pytest.raises(RuntimeResultError, match="codex_turn_failed") as raised:
        async for event in runtime.execute(_context(tmp_path)):
            events.append(event)

    assert raised.value.error_code == "codex_context_window_exceeded"
    assert events[-1].type == "runtime.thread.invalidated"
    assert process.closed is True


@pytest.mark.asyncio
async def test_tool_call_budget_interrupts_repetitive_codex_loop(tmp_path: Path) -> None:
    messages = [
        _notification(
            "item/started",
            {
                "item": {
                    "id": f"call-{index}",
                    "type": "mcpToolCall",
                    "server": "search",
                    "tool": "page",
                    "arguments": {"page": index},
                }
            },
        )
        for index in range(1, 4)
    ]
    client = FakeClient(messages)
    runtime, process, _options = _runtime(tmp_path, client, max_tool_calls=2)
    events = []

    with pytest.raises(RuntimeResultError, match="codex_tool_call_limit"):
        async for event in runtime.execute(_context(tmp_path)):
            events.append(event)

    assert [method for method, _params in client.requests][-1] == "turn/interrupt"
    assert [event.type for event in events[-2:]] == [
        "runtime.error",
        "runtime.thread.invalidated",
    ]
    assert process.closed is True


@pytest.mark.asyncio
async def test_turn_timeout_closes_process(tmp_path: Path) -> None:
    client = FakeClient([], hang_turn=True)
    runtime, process, _options = _runtime(tmp_path, client, timeout=0.01)

    with pytest.raises(RuntimeExecutionTimeoutError, match="timed out"):
        _ = [event async for event in runtime.execute(_context(tmp_path))]

    assert process.closed is True


@pytest.mark.asyncio
async def test_guidance_targets_active_turn_without_interrupting(tmp_path: Path) -> None:
    from tests.unit.runtime.test_steering import setup_inbox

    _, _, service, inbox = await setup_inbox()
    accepted = asyncio.Event()

    class GuidableClient(FakeClient):
        async def request(self, method, params=None, *, timeout_seconds=None):
            if method == "turn/steer":
                self.requests.append((method, dict(params or {})))
                accepted.set()
                return {"turnId": "turn-1"}
            return await super().request(method, params, timeout_seconds=timeout_seconds)

        async def inbound(self):
            await service.steer("t", "r", "guidance", "use the new constraints")
            async with asyncio.timeout(2):
                await accepted.wait()
            yield _notification("turn/completed", {"turn": {"status": "completed"}})

    client = GuidableClient([])
    runtime, process, _ = _runtime(tmp_path, client)
    context = _context(tmp_path).model_copy(update={"steering": inbox})
    events = [event async for event in runtime.execute(context)]
    assert events[-1].type == "runtime.turn.completed"
    assert (
        "turn/steer",
        {
            "threadId": "thread-1",
            "expectedTurnId": "turn-1",
            "input": [{"type": "text", "text": "use the new constraints"}],
        },
    ) in client.requests
    assert not any(method == "turn/interrupt" for method, _ in client.requests)
    assert process.closed and inbox.closed
