"""execd PTY WebSocket session: framing, shell line building and teardown."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from harness.sandbox.opensandbox_session import (
    OpenSandboxPtySession,
    OpenSandboxSessionError,
)

EXECD_PORT = 44_772


class FakeSocket:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[Any] = []
        self.closed = False

    async def send(self, value: bytes | str) -> None:
        self.sent.append(value)

    async def close(self) -> None:
        self.closed = True
        self.queue.put_nowait(None)

    def __aiter__(self) -> "FakeSocket":
        return self

    async def __anext__(self) -> Any:
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    def feed(self, frame: Any) -> None:
        self.queue.put_nowait(frame)


def handler(recorded: list[httpx.Request], *, pty_id: str = "pty-1") -> Any:
    def respond(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.url.path.endswith("/pty") and request.method == "POST":
            return httpx.Response(201, json={"session_id": pty_id})
        return httpx.Response(200, json={})

    return respond


def build(recorded: list[httpx.Request], socket: FakeSocket) -> tuple[
    OpenSandboxPtySession, list[tuple[str, dict[str, Any]]]
]:
    client = httpx.AsyncClient(
        base_url="http://sandbox.example:8090",
        headers={"OPEN-SANDBOX-API-KEY": "secret"},
        transport=httpx.MockTransport(handler(recorded)),
    )
    connections: list[tuple[str, dict[str, Any]]] = []

    async def connect(url: str, **kwargs: Any) -> FakeSocket:
        connections.append((url, kwargs))
        return socket

    session = OpenSandboxPtySession(
        client=client,
        sandbox_id="os-1",
        execd_port=EXECD_PORT,
        connector=connect,
    )
    return session, connections


def connected_frame(session_id: str = "pty-1") -> str:
    return json.dumps({"type": "connected", "session_id": session_id, "mode": "pipe"})


@pytest.mark.asyncio
async def test_start_opens_the_socket_and_types_the_command_line() -> None:
    recorded: list[httpx.Request] = []
    socket = FakeSocket()
    session, connections = build(recorded, socket)
    socket.feed(connected_frame())

    await session.start(
        ["/root/.local/bin/claude", "--input-format", "stream-json"],
        "/workspace/run-a",
        {"FOO": "bar baz", "CLAUDE_CONFIG_DIR": "/workspace/.claude-config"},
    )

    assert connections[0][0] == (
        f"ws://sandbox.example:8090/v1/sandboxes/os-1/proxy/{EXECD_PORT}/pty/pty-1/ws?pty=0"
    )
    headers = {
        key.lower(): value
        for key, value in connections[0][1]["additional_headers"].items()
    }
    assert headers == {"open-sandbox-api-key": "secret"}
    pty_request = next(r for r in recorded if r.url.path.endswith("/pty"))
    assert json.loads(pty_request.content) == {"cwd": "/workspace/run-a"}
    line = socket.sent[0]
    assert isinstance(line, bytes)
    assert line[0:1] == b"\x00"
    text = line[1:].decode()
    assert "cd /workspace/run-a" in text
    assert "export FOO='bar baz'" in text
    assert "exec /root/.local/bin/claude --input-format stream-json" in text


@pytest.mark.asyncio
async def test_a_second_start_is_refused() -> None:
    socket = FakeSocket()
    session, _ = build([], socket)
    socket.feed(connected_frame())
    await session.start(["/bin/true"], "/workspace", {})
    with pytest.raises(OpenSandboxSessionError, match="already started"):
        await session.start(["/bin/true"], "/workspace", {})


@pytest.mark.asyncio
async def test_an_invalid_environment_name_is_rejected() -> None:
    socket = FakeSocket()
    session, _ = build([], socket)
    socket.feed(connected_frame())
    with pytest.raises(ValueError, match="invalid remote environment name"):
        await session.start(["/bin/true"], "/workspace", {"A B": "x"})


@pytest.mark.asyncio
async def test_frames_are_split_into_streams_and_the_exit_status() -> None:
    socket = FakeSocket()
    session, _ = build([], socket)
    socket.feed(connected_frame())
    await session.start(["/bin/true"], "/workspace", {})

    socket.feed(b"\x01hello\n")
    socket.feed(b"\x02oops\n")
    socket.feed(b"\x03replayed")  # viewer replay frames carry no session output
    socket.feed(b"\x01world")
    socket.feed(json.dumps({"type": "exit", "exit_code": 7}))

    assert await session.read_stdout() == b"hello\n"
    assert await session.read_stderr() == b"oops\n"
    assert await session.read_stdout() == b"world"
    assert await session.wait() == 7
    assert await session.read_stdout() is None


@pytest.mark.asyncio
async def test_a_socket_that_ends_without_an_exit_frame_reports_failure() -> None:
    socket = FakeSocket()
    session, _ = build([], socket)
    socket.feed(connected_frame())
    await session.start(["/bin/true"], "/workspace", {})
    await socket.close()

    assert await session.wait() == 1
    assert await session.read_stdout() is None


@pytest.mark.asyncio
async def test_terminate_signals_closes_and_deletes_the_pty_session() -> None:
    recorded: list[httpx.Request] = []
    socket = FakeSocket()
    session, _ = build(recorded, socket)
    socket.feed(connected_frame())
    await session.start(["/bin/true"], "/workspace", {})

    await session.terminate()

    assert json.loads(socket.sent[-1]) == {"type": "signal", "signal": "SIGINT"}
    assert socket.closed
    assert await session.wait() == 0
    deleted = [r for r in recorded if r.method == "DELETE"]
    assert deleted and deleted[0].url.path.endswith("/pty/pty-1")


@pytest.mark.asyncio
async def test_stage_config_creates_a_private_directory_and_rejects_escapes() -> None:
    recorded: list[httpx.Request] = []
    socket = FakeSocket()
    session, _ = build(recorded, socket)

    await session.stage_config(
        "/workspace/.claude-config/session-a", {"sub/state.json": b"{}"}
    )
    directories = [r for r in recorded if r.url.path.endswith("/directories")]
    assert json.loads(directories[0].content) == {
        "/workspace/.claude-config/session-a": {"mode": 700}
    }
    upload = next(r for r in recorded if r.url.path.endswith("/files/upload"))
    assert b"/workspace/.claude-config/session-a/sub/state.json" in upload.content
    assert b'"mode": 600' in upload.content

    with pytest.raises(ValueError, match="escaped config directory"):
        await session.stage_config("/workspace/config", {"../escape": b"x"})
    with pytest.raises(ValueError, match="must be absolute"):
        await session.stage_config("relative", {"state.json": b"x"})


@pytest.mark.asyncio
async def test_an_inline_mcp_config_becomes_a_remote_file() -> None:
    recorded: list[httpx.Request] = []
    socket = FakeSocket()
    session, _ = build(recorded, socket)
    socket.feed(connected_frame())

    await session.start(
        ["/root/.local/bin/claude"],
        "/workspace/run-a",
        {
            "CLAUDE_CONFIG_DIR": "/workspace/.claude-config/session-a",
            "HARNESS_CLAUDE_MCP_CONFIG": '{"mcpServers":{"harness-sandbox":{}}}',
        },
    )

    upload = next(r for r in recorded if r.url.path.endswith("/files/upload"))
    assert b"harness-sandbox" in upload.content
    assert b'"mode": 600' in upload.content
    line = socket.sent[0][1:].decode()
    assert "--mcp-config /workspace/.claude-config/session-a/harness-mcp-" in line
    assert "HARNESS_CLAUDE_MCP_CONFIG" not in line


@pytest.mark.asyncio
async def test_a_socket_url_keeps_the_configured_port() -> None:
    socket = FakeSocket()
    session, connections = build([], socket)
    socket.feed(connected_frame())
    await session.start(["/bin/true"], "/workspace", {})
    assert connections[0][0].startswith("ws://sandbox.example:8090/")
