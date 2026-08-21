import asyncio
import json
from typing import cast

import pytest

from harness.runtime.codex_app_server import (
    AsyncJsonlWriter,
    CodexConnectionClosedError,
    CodexJsonlClient,
    CodexRpcRemoteError,
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
