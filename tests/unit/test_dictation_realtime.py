import asyncio
import json

import pytest
from websockets.asyncio.server import ServerConnection, serve

from harness.dictation.realtime import RealtimeDictation
from harness.dictation.service import DictationError, DictationSettings


@pytest.mark.asyncio
async def test_32_concurrent_sessions_keep_results_isolated_and_reclaim_capacity() -> None:
    async def upstream(socket: ServerConnection) -> None:
        transcript = ""
        async for message in socket:
            if message == "START":
                await socket.send(json.dumps({"event": "started"}))
            elif isinstance(message, bytes):
                transcript = f"用户{int.from_bytes(message, 'little')}的语音"
                await socket.send(json.dumps({"sentences": [], "partial": transcript}))
            elif message == "STOP":
                await socket.send(json.dumps({
                    "sentences": [{"text": transcript + "。"}], "partial": "",
                    "is_final": True,
                }))
                await socket.send(json.dumps({"event": "stopped"}))

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        settings = DictationSettings(realtime_url=f"ws://127.0.0.1:{port}")
        assert settings.max_active_sessions == 32
        manager = RealtimeDictation(settings)
        try:
            owners = [(f"tenant-{index % 2}", f"user-{index}") for index in range(32)]
            sessions = await asyncio.gather(*(manager.start(owner) for owner in owners))
            assert len(manager.sessions) == 32
            assert len({session.session_id for session in sessions}) == 32
            with pytest.raises(DictationError, match="繁忙"):
                await manager.start(("tenant-extra", "user-extra"))
            with pytest.raises(DictationError, match="不存在"):
                manager.get(sessions[0].session_id, owners[1])
            await asyncio.gather(*(
                manager.send(session, 0, index.to_bytes(2, "little"))
                for index, session in enumerate(sessions)
            ))
            drafts = await asyncio.gather(*(session.events.get() for session in sessions))
            assert [item["text"] for item in drafts] == [
                f"用户{index}的语音" for index in range(32)
            ]
            assert await manager.finish(sessions[0]) == "用户0的语音。"
            # The SSE route releases a session after delivering its done event.
            await manager.cancel(sessions[0].session_id)
            replacement = await manager.start(("tenant-extra", "user-extra"))
            assert len(manager.sessions) == 32
            await manager.cancel(replacement.session_id)
            finals = await asyncio.gather(*(manager.finish(session) for session in sessions[1:]))
            assert finals == [f"用户{index}的语音。" for index in range(1, 32)]
            await asyncio.gather(*(manager.cancel(session.session_id) for session in sessions[1:]))
            assert not manager.sessions
        finally:
            await manager.close()


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


@pytest.mark.asyncio
async def test_sliding_partial_windows_keep_front_text_until_authoritative_final() -> None:
    first = "请先查看上海的数据，然后再查看武汉的数据"
    tail = "然后再查看武汉的数据，并与北京对比。"

    async def upstream(socket: ServerConnection) -> None:
        async for message in socket:
            if message == "START":
                await socket.send(json.dumps({"event": "started"}))
            elif isinstance(message, bytes):
                partial, start = (first, 290) if message[0] == 0 else (tail, 1600)
                await socket.send(json.dumps({
                    "sentences": [], "partial": partial, "partial_start_ms": start,
                    "is_final": False,
                }))
            elif message == "STOP":
                await socket.send(json.dumps({
                    "sentences": [{"text": "查看上海、武汉和北京的数据。"}], "partial": "",
                    "partial_start_ms": 0, "is_final": True,
                }))
                await socket.send(json.dumps({"event": "stopped"}))

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        session = await manager.start(("tenant-a", "user-a"))
        await manager.send(session, 0, bytes(3200))
        assert (await session.events.get())["text"] == first
        await manager.send(session, 1, b"\x01\x00" + bytes(3198))
        assert (await session.events.get())["text"] == "请先查看上海的数据，" + tail.rstrip("。")
        assert await manager.finish(session) == "查看上海、武汉和北京的数据。"
        await manager.close()
