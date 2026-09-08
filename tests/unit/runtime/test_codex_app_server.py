import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from harness.runtime.codex_app_server import (
    AsyncJsonlWriter,
    CodexAppServerOptions,
    CodexConnectionClosedError,
    CodexJsonlClient,
    CodexRpcRemoteError,
    CodexRpcRequestTimeoutError,
    DaytonaCodexAppServerProcess,
)
from harness.runtime.codex_protocol import CodexMessageKind, CodexProtocolError


class FakeWriter:
    def __init__(self) -> None:
        self.lines: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.lines.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed


def _client() -> tuple[asyncio.StreamReader, FakeWriter, CodexJsonlClient]:
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    client = CodexJsonlClient(reader, cast(AsyncJsonlWriter, writer))
    client.start()
    return reader, writer, client


def _feed(reader: asyncio.StreamReader, value: object) -> None:
    reader.feed_data(json.dumps(value).encode("utf-8") + b"\n")


@pytest.mark.asyncio
async def test_correlates_request_response_and_writes_compact_jsonl() -> None:
    reader, writer, client = _client()
    request = asyncio.create_task(client.request("thread/start", {"model": "gpt-test"}))
    await asyncio.sleep(0)

    assert json.loads(writer.lines[0]) == {
        "id": 1,
        "method": "thread/start",
        "params": {"model": "gpt-test"},
    }

    _feed(reader, {"id": 1, "result": {"thread": {"id": "thr-1"}}})

    assert await request == {"thread": {"id": "thr-1"}}
    await client.close()


@pytest.mark.asyncio
async def test_request_timeout_reports_control_plane_method() -> None:
    reader, _writer, client = _client()

    with pytest.raises(CodexRpcRequestTimeoutError) as raised:
        await client.request("thread/start", {}, timeout_seconds=0.01)

    assert raised.value.method == "thread/start"
    assert raised.value.timeout_seconds == 0.01
    reader.feed_eof()
    await client.close()


@pytest.mark.asyncio
async def test_streams_notifications_and_server_requests() -> None:
    reader, _writer, client = _client()
    _feed(reader, {"method": "turn/started", "params": {"turn": {"id": "turn-1"}}})
    _feed(
        reader,
        {
            "id": 90,
            "method": "item/fileChange/requestApproval",
            "params": {"itemId": "patch-1"},
        },
    )
    inbound = client.inbound()
    messages = [await anext(inbound), await anext(inbound)]

    assert [message.kind for message in messages] == [
        CodexMessageKind.NOTIFICATION,
        CodexMessageKind.SERVER_REQUEST,
    ]
    assert messages[1].payload["id"] == 90
    await client.close()


@pytest.mark.asyncio
async def test_returns_content_minimized_remote_error() -> None:
    reader, _writer, client = _client()
    request = asyncio.create_task(client.request("turn/start", {}))
    await asyncio.sleep(0)
    _feed(
        reader,
        {
            "id": 1,
            "error": {
                "code": -32000,
                "message": "Authorization token=private-value",
                "data": {"secret": "never-show"},
            },
        },
    )

    with pytest.raises(CodexRpcRemoteError) as captured:
        await request

    assert captured.value.code == -32000
    assert captured.value.method == "turn/start"
    assert captured.value.remote_message == "[redacted sdk diagnostic]"
    assert "private-value" not in repr(captured.value)
    assert "never-show" not in repr(captured.value)
    await client.close()


@pytest.mark.asyncio
async def test_invalid_json_fails_pending_requests_and_inbound_stream() -> None:
    reader, _writer, client = _client()
    request = asyncio.create_task(client.request("thread/start", {}))
    await asyncio.sleep(0)
    reader.feed_data(b"not-json\n")

    with pytest.raises(CodexProtocolError):
        await request
    with pytest.raises(CodexProtocolError):
        _ = [message async for message in client.inbound()]
    await client.close()


@pytest.mark.asyncio
async def test_eof_fails_pending_requests() -> None:
    reader, _writer, client = _client()
    request = asyncio.create_task(client.request("thread/start", {}))
    await asyncio.sleep(0)
    reader.feed_eof()

    with pytest.raises(CodexConnectionClosedError):
        await request
    await client.close()


@pytest.mark.asyncio
async def test_writes_notifications_and_server_responses() -> None:
    _reader, writer, client = _client()

    await client.notify("initialized", {})
    await client.respond(7, {"decision": "accept"})
    await client.respond_error(8, code=-32601, message="unsupported")

    assert [json.loads(line) for line in writer.lines] == [
        {"method": "initialized", "params": {}},
        {"id": 7, "result": {"decision": "accept"}},
        {"id": 8, "error": {"code": -32601, "message": "unsupported"}},
    ]
    await client.close()


@pytest.mark.asyncio
async def test_enforces_outbound_line_limit() -> None:
    _reader, _writer, client = _client()
    client._max_line_bytes = 64  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(CodexProtocolError, match="line limit"):
        await client.notify("turn/start", {"content": "x" * 100})
    await client.close()


@pytest.mark.asyncio
async def test_daytona_process_bootstraps_pinned_package_and_bridges_jsonl() -> None:
    class FakeRemoteSession:
        def __init__(self) -> None:
            self.argv: list[str] = []
            self.cwd = ""
            self.environment: dict[str, str] = {}
            self.writes: list[str] = []
            self.stdout: asyncio.Queue[bytes | None] = asyncio.Queue()
            self.stderr: asyncio.Queue[bytes | None] = asyncio.Queue()
            self.ended = False
            self.terminated = False

        async def stage_config(self, _remote: str, _files: dict[str, bytes]) -> None:
            return None

        async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
            self.argv = argv
            self.cwd = cwd
            self.environment = env

        async def write(self, data: str) -> None:
            self.writes.append(data)
            payload = json.loads(data)
            if payload.get("method") == "initialize":
                await self.stdout.put(
                    json.dumps({"id": payload["id"], "result": {"ok": True}}).encode() + b"\n"
                )

        async def end_input(self) -> None:
            self.ended = True
            await self.stdout.put(None)

        async def read_stdout(self) -> bytes | None:
            return await self.stdout.get()

        async def read_stderr(self) -> bytes | None:
            return await self.stderr.get()

        async def wait(self) -> int:
            return 0

        async def terminate(self) -> None:
            self.terminated = True

    session = FakeRemoteSession()
    process = DaytonaCodexAppServerProcess(
        session=session,  # type: ignore[arg-type]
        options=CodexAppServerOptions(
            codex_path=Path("/local/codex"),
            working_directory=Path("/remote/workspace"),
            environment={"HARNESS_CODEX_PROVIDER_API_KEY": "private"},
            config_overrides=('model_provider="agent_studio"',),
        ),
        remote_workspace="/remote/workspace",
        cli_path="/home/daytona/.local/bin/codex",
        cli_version="0.149.0",
        cli_sha256="abc123",
    )

    result = await process.start()

    assert result == {"ok": True}
    assert session.cwd == "/remote/workspace"
    assert session.environment == {
        "HARNESS_CODEX_PROVIDER_API_KEY": "private",
        "HOME": "/remote/workspace/.codex-home",
    }
    assert "codex-package-x86_64-unknown-linux-musl.tar.gz" in session.argv[2]
    assert session.argv[-3:] == [
        "app-server",
        "--config",
        'model_provider="agent_studio"',
    ]
    assert json.loads(session.writes[1]) == {"method": "initialized", "params": {}}

    await process.close()

    assert session.ended is True
    assert session.terminated is True
