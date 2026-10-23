import asyncio
import io
import json
import wave

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from harness.dictation.qwen_bridge import QwenBridge, pcm_wav
from harness.dictation.realtime import RealtimeDictation
from harness.dictation.service import DictationSettings, VadResult, validate_wav


class Speech:
    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, audio: bytes) -> VadResult:
        return VadResult(speech=True, endpoint=False, duration=validate_wav(audio, 20))

    async def transcribe(self, audio: bytes) -> str:
        self.calls += 1
        assert validate_wav(audio, 20) > 0
        return "Qwen3-ASR-1.7B"


@pytest.mark.asyncio
async def test_existing_api_manager_receives_live_qwen_draft_and_final_without_duplication() -> (
    None
):
    bridge = QwenBridge(DictationSettings(), interval=0.01)
    speech = Speech()
    bridge.adapter = speech  # type: ignore[assignment]
    async with serve(bridge.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        session = await manager.start(("tenant", "user"))
        await manager.send(session, 0, b"\x01\x00" * 16000)
        event = await asyncio.wait_for(session.events.get(), 1)
        assert event == {"type": "draft", "text": "Qwen3-ASR-1.7B"}
        assert not session.finishing
        final = await manager.finish(session)
        assert final.strip() == "Qwen3-ASR-1.7B"
        assert speech.calls == 1
        await manager.close()
    assert bridge.active == 0


@pytest.mark.asyncio
async def test_concurrent_clients_keep_audio_and_transcripts_separate_and_cancel_cleanly() -> None:
    bridge = QwenBridge(DictationSettings(max_active_sessions=2), interval=0.01)
    bridge.adapter = Speech()  # type: ignore[assignment]
    async with serve(bridge.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        first = await manager.start(("tenant-a", "user"))
        second = await manager.start(("tenant-b", "user"))
        await manager.send(first, 0, b"\x01\x00" * 16000)
        await asyncio.wait_for(first.events.get(), 1)
        assert second.events.empty() and second.text == ""
        async with connect(f"ws://127.0.0.1:{port}") as rejected:
            assert json.loads(await rejected.recv())["event"] == "error"
        assert (await manager.finish(second)) == ""
        await manager.close()
    assert bridge.active == 0


@pytest.mark.asyncio
async def test_oversized_audio_is_rejected_before_reaching_asr() -> None:
    bridge = QwenBridge(DictationSettings(), interval=0.01)
    speech = Speech()
    bridge.adapter = speech  # type: ignore[assignment]
    async with serve(bridge.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}") as socket:
            await socket.send("START")
            assert json.loads(await socket.recv())["event"] == "started"
            await socket.send(b"odd")
            assert json.loads(await socket.recv())["event"] == "error"
    assert speech.calls == 0 and bridge.active == 0
    assert validate_wav(pcm_wav(b"\x00\x00" * 16000), 20) == 1


@pytest.mark.asyncio
async def test_utterance_rollover_preserves_prior_text_and_silence_never_calls_asr() -> None:
    class Segments(Speech):
        async def detect(self, audio: bytes) -> VadResult:
            with wave.open(io.BytesIO(audio)) as wav:
                speech = bool(any(wav.readframes(wav.getnframes())))
            return VadResult(speech=speech, endpoint=False, duration=1)

        async def transcribe(self, audio: bytes) -> str:
            self.calls += 1
            with wave.open(io.BytesIO(audio)) as wav:
                return "第一段。" if wav.readframes(1) == b"\x01\x00" else "第二段。"

    bridge = QwenBridge(DictationSettings(max_audio_seconds=1), interval=0.01)
    speech = Segments()
    bridge.adapter = speech  # type: ignore[assignment]
    async with serve(bridge.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        manager = RealtimeDictation(DictationSettings(realtime_url=f"ws://127.0.0.1:{port}"))
        session = await manager.start(("tenant", "user"))
        await manager.send(session, 0, b"\x01\x00" * 16000)
        assert (await asyncio.wait_for(session.events.get(), 1))["text"] == "第一段。"
        await manager.send(session, 1, b"\x02\x00" * 16000)
        assert (await asyncio.wait_for(session.events.get(), 1))["text"] == "第一段。第二段。"
        await manager.send(session, 2, b"\x00\x00" * 16000)
        assert await manager.finish(session) == "第一段。第二段。"
        assert speech.calls == 2
        await manager.close()
