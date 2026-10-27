"""RFB client protocol behaviour against an in-process fake desktop server."""

import asyncio
import struct

import pytest

from harness.computer.desktop_client import (
    RfbClient,
    click,
    keysym_for,
    press_keys,
    scroll,
    type_text,
)


class _FakeWriter:
    def __init__(self, sink) -> None:
        self._sink = sink

    def write(self, data: bytes) -> None:
        self._sink(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        return None


class _SinkTransport:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader, self._writer = reader, writer

    async def open(self):
        return self._reader, self._writer


@pytest.mark.asyncio
async def test_client_performs_handshake_and_requests_raw_frames():
    reader = asyncio.StreamReader()
    sent = bytearray()

    class Transport:
        async def open(self):
            return reader, _FakeWriter(sent.extend)

    client = RfbClient(Transport())
    reader.feed_data(b"RFB 003.008\n")
    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0.05)
    assert bytes(sent) == b"RFB 003.008\n"
    reader.feed_data(bytes([1, 1]) + struct.pack(">I", 0))
    pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    reader.feed_data(struct.pack(">HH", 4, 2) + pixel_format + struct.pack(">I", 7) + b"fixture")
    await asyncio.wait_for(connect_task, timeout=5)
    assert (client.width, client.height) == (4, 2)
    # choice (1) + ClientInit (1) + SetPixelFormat (20) + SetEncodings (8).
    rest = bytes(sent[12:])
    assert rest[0] == 1 and rest[1] == 1 and len(rest) >= 30
    assert struct.unpack(">i", rest[22 + 4 : 22 + 8])[0] == 0  # raw encoding
    await client.close()


@pytest.mark.asyncio
async def test_screenshot_assembles_blitted_frame():
    width, height = 2, 2
    reader = asyncio.StreamReader()
    sent = bytearray()

    class Transport:
        async def open(self):
            writer = _FakeWriter(sent.extend)
            return reader, writer

    client = RfbClient(Transport())
    reader.feed_data(b"RFB 003.008\n")
    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0.05)
    reader.feed_data(bytes([1, 1]) + struct.pack(">I", 0))
    pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    reader.feed_data(struct.pack(">HH", width, height) + pixel_format + struct.pack(">I", 0))
    await asyncio.wait_for(connect_task, timeout=5)

    # Blue, then red pixels in BGRX order; the update rect covers one row.
    row = bytes.fromhex("ff000000") + bytes.fromhex("0000ff00")
    async def serve_update() -> None:
        await asyncio.sleep(0.05)
        reader.feed_data(struct.pack(">BxH", 0, 1))
        reader.feed_data(struct.pack(">HHHHi", 0, 0, width, height, 0))
        reader.feed_data(row + row)

    server_task = asyncio.create_task(serve_update())
    result_width, result_height, png = await asyncio.wait_for(client.screenshot(), timeout=5)
    await server_task
    assert (result_width, result_height) == (width, height)
    assert png.startswith(b"\x89PNG")
    await client.close()


@pytest.mark.asyncio
async def test_input_helpers_send_pointer_and_key_events():
    reader = asyncio.StreamReader()
    sent = bytearray()

    class Transport:
        async def open(self):
            return reader, _FakeWriter(sent.extend)

    client = RfbClient(Transport())
    reader.feed_data(b"RFB 003.008\n")
    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0.05)
    reader.feed_data(bytes([1, 1]) + struct.pack(">I", 0))
    pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    reader.feed_data(struct.pack(">HH", 100, 100) + pixel_format + struct.pack(">I", 0))
    await asyncio.wait_for(connect_task, timeout=5)

    await click(client, 10, 20, "left")
    await scroll(client, 10, 20, "down", 1)
    await type_text(client, "a中")
    await press_keys(client, ["ctrl", "c"])

    stream = bytes(sent[42:])  # version + choice + init + pixel format + encodings
    pointer_count = 0
    key_events: list[tuple[int, bool]] = []
    offset = 0
    while offset + 1 <= len(stream):
        message_type = stream[offset]
        if message_type == 5:
            mask, x, y = struct.unpack(">BHH", stream[offset + 1 : offset + 6])
            pointer_count += 1
            offset += 6
            assert x < 100 and y < 100
        elif message_type == 4:
            down = stream[offset + 1]
            keysym = struct.unpack(">I", stream[offset + 4 : offset + 8])[0]
            key_events.append((keysym, down == 1))
            offset += 8
        else:
            break
    assert pointer_count >= 4  # click press/release + wheel press/release
    # 'a' press+release, U+4E2D press+release, ctrl down, c down, c up, ctrl up.
    assert key_events == [
        (ord("a"), True), (ord("a"), False),
        (0x01000000 + 0x4E2D, True), (0x01000000 + 0x4E2D, False),
        (0xFFE3, True), (ord("c"), True), (ord("c"), False), (0xFFE3, False),
    ]
    await client.close()


def test_keysym_mapping_covers_named_and_unicode():
    assert keysym_for("Enter") == 0xFF0D
    assert keysym_for("CTRL") == 0xFFE3
    assert keysym_for("control") == 0xFFE3
    assert keysym_for("a") == ord("a")
    assert keysym_for("中") == 0x01000000 + 0x4E2D
    assert keysym_for("not-a-key") is None
