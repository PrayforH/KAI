import json

import pytest
from httpx import ASGITransport, AsyncClient
from websockets.asyncio.server import ServerConnection, serve

from harness.api.app import create_memory_app
from harness.dictation.realtime import RealtimeDictation
from harness.dictation.service import DictationService, DictationSettings


@pytest.mark.asyncio
async def test_realtime_http_sessions_require_identity_and_keep_audio_order() -> None:
    async def upstream(socket: ServerConnection) -> None:
        async for message in socket:
            if message == "START":
                await socket.send(json.dumps({"event": "started"}))
            elif message == "STOP":
                await socket.send(json.dumps({"sentences": [{"text": "尾句。"}], "partial": ""}))
                await socket.send(json.dumps({"event": "stopped"}))

    async with serve(upstream, "127.0.0.1", 0) as server:
        app = create_memory_app()
        settings = DictationSettings(
            enabled=True,
            engine="funasr_realtime",
            realtime_url=f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        )
        app.state.dictation = DictationService(settings)
        manager = RealtimeDictation(settings)
        app.state.dictation_realtime = manager
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/v1/dictation/sessions")).status_code == 401
            headers = {"X-Tenant-ID": "a", "X-User-ID": "owner"}
            start = await client.post("/v1/dictation/sessions", headers=headers)
            assert start.status_code == 201
            path = f"/v1/dictation/sessions/{start.json()['id']}"
            other = {"X-Tenant-ID": "b", "X-User-ID": "owner"}
            assert (await client.post(path + "/finish", headers=other)).status_code == 404
            assert (
                await client.post(path + "/audio?sequence=1", headers=headers, content=bytes(32))
            ).status_code == 409
            assert (
                await client.post(path + "/audio?sequence=0", headers=headers, content=bytes(32))
            ).status_code == 200
            response = await client.post(path + "/finish", headers=headers)
            assert response.json() == {"text": "尾句。"}
            assert not manager.sessions
