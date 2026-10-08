import json

import pytest
from websockets.asyncio.server import ServerConnection, serve

from harness.dictation.realtime import RealtimeDictation
from harness.dictation.service import DictationError, DictationSettings


@pytest.mark.asyncio
async def test_official_protocol_replaces_partial_flushes_tail_and_scopes_owner() -> None:
    received: list[str | bytes] = []

    async def upstream(socket: ServerConnection) -> None:
        async for message in socket:
            received.append(message)
            if message == "START":
                await socket.send(json.dumps({"event": "started"}))
            elif isinstance(message, bytes):
                await socket.send(json.dumps({"sentences": [], "partial": "请不要改金额1200"}))
            elif message == "STOP":
                await socket.send(
                    json.dumps(
                        {
                            "sentences": [{"text": "请不要改金额1200元。"}],
                            "partial": "",
                            "is_final": True,
                        }
                    )
                )
                await socket.send(json.dumps({"event": "stopped"}))

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        session = await manager.start(("tenant-a", "user-a"))
        with pytest.raises(DictationError):
            manager.get(session.session_id, ("tenant-b", "user-a"))
        with pytest.raises(DictationError):
            await manager.start(("tenant-a", "user-a"))
        with pytest.raises(DictationError):
            await manager.send(session, 1, bytes(3200))
        with pytest.raises(DictationError):
            await manager.send(session, 0, bytes(32001))
        await manager.send(session, 0, bytes(3200))
        first = await session.events.get()
        assert first == {"type": "draft", "text": "请不要改金额1200"}
        assert await manager.finish(session) == "请不要改金额1200元。"
        assert await session.events.get() == {"type": "draft", "text": "请不要改金额1200元。"}
        assert await session.events.get() == {"type": "done", "text": "请不要改金额1200元。"}
        assert received == ["START", bytes(3200), "STOP"]
        await manager.close()
        assert not manager.sessions


@pytest.mark.asyncio
async def test_cancel_closes_connection_without_stop_or_final_recognition() -> None:
    received: list[str | bytes] = []

    async def upstream(socket: ServerConnection) -> None:
        async for message in socket:
            received.append(message)
            await socket.send(json.dumps({"event": "started"}))

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        session = await manager.start(("tenant-a", "user-a"))
        await manager.cancel(session.session_id)
        assert received == ["START"]
        assert session.stopped.is_set()
        assert not manager.sessions


@pytest.mark.asyncio
async def test_upstream_disconnect_preserves_preview_and_reports_failure() -> None:
    async def upstream(socket: ServerConnection) -> None:
        await socket.recv()
        await socket.send(json.dumps({"event": "started"}))
        await socket.send(json.dumps({"sentences": [], "partial": "已识别文字"}))
        await socket.close()

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        session = await manager.start(("tenant-a", "user-a"))
        await session.stopped.wait()
        assert session.text == "已识别文字"
        with pytest.raises(DictationError, match="中断"):
            await manager.finish(session)
        await manager.close()
