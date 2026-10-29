"""Incremental Qwen file-ASR adapter for the existing private dictation WS protocol.

This is utterance-incremental transcription, not native acoustic streaming. Each
socket owns its PCM, VAD state and transcript; only bounded audio snapshots reach
the configured gateway. No audio or recognized text is written to logs or disk.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from dataclasses import dataclass, field
from http import HTTPStatus

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from harness.dictation.service import DictationError, DictationService, DictationSettings


def pcm_wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(pcm)
    return output.getvalue()


@dataclass
class Utterance:
    pending: bytearray = field(default_factory=bytearray)
    sentences: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())
    offset_bytes: int = 0
    total_bytes: int = 0
    finishing: bool = False


class QwenBridge:
    def __init__(self, settings: DictationSettings, *, interval: float = 1.5) -> None:
        self.settings = settings
        self.adapter = DictationService(settings.model_copy(update={"engine": "gateway"}))
        self.interval = interval
        self.active = 0

    async def decode(self, state: Utterance, socket: ServerConnection) -> None:
        last_size = 0
        partial = ""
        maximum = min(12, self.settings.max_audio_seconds) * 32_000
        while True:
            await asyncio.sleep(self.interval)
            if not state.pending:
                if state.finishing:
                    await socket.send(json.dumps({"event": "stopped"}))
                    return
                continue
            snapshot = bytes(state.pending[:maximum])
            if len(snapshot) < 16_000 and not state.finishing:
                continue
            final = state.finishing or len(snapshot) == maximum
            if len(snapshot) != last_size:
                audio = pcm_wav(snapshot)
                vad = await self.adapter.detect(audio)
                final = final or vad.endpoint or not vad.speech
                partial = await self.adapter.transcribe(audio) if vad.speech else ""
                last_size = len(snapshot)
            if final:
                if partial:
                    # Separate ASCII word boundaries across forced utterance cuts.
                    text = partial + (
                        " " if partial[-1].isascii() and partial[-1].isalnum() else ""
                    )
                    state.sentences.append({"text": text})
                del state.pending[: len(snapshot)]
                state.offset_bytes += len(snapshot)
                partial, last_size = "", 0
            if sum(len(item["text"]) for item in state.sentences) + len(partial) > 12_000:
                raise DictationError("语音文字过长，请分次输入")
            await socket.send(
                json.dumps(
                    {
                        "sentences": state.sentences,
                        "partial": partial,
                        "partial_start_ms": state.offset_bytes // 32,
                        "is_final": final,
                    },
                    ensure_ascii=False,
                )
            )

    async def receive(self, state: Utterance, socket: ServerConnection) -> None:
        async for content in socket:
            if content == "STOP":
                state.finishing = True
                return
            if not isinstance(content, bytes) or not content or len(content) % 2:
                raise DictationError("需要有序 PCM16 音频")
            if len(content) > 32_000:
                raise DictationError("单个音频片段过长")
            state.total_bytes += len(content)
            if state.total_bytes > self.settings.max_session_seconds * 32_000:
                raise DictationError("录音已达到时长上限")
            if len(state.pending) + len(content) > self.settings.max_audio_seconds * 32_000:
                raise DictationError("语音服务响应较慢，请分次输入")
            state.pending.extend(content)
        raise DictationError("录音连接已中断")

    async def handle(self, socket: ServerConnection) -> None:
        if self.active >= self.settings.max_active_sessions:
            await socket.send(json.dumps({"event": "error"}))
            await socket.close()
            return
        self.active += 1
        try:
            async with asyncio.timeout(self.settings.max_session_seconds + 25):
                if await asyncio.wait_for(socket.recv(), 5) != "START":
                    raise DictationError("需要先启动录音")
                await socket.send(
                    json.dumps(
                        {
                            "event": "started",
                            "model": self.settings.gateway_model,
                            "mode": "utterance_incremental",
                        }
                    )
                )
                state = Utterance()
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(self.receive(state, socket))
                    tasks.create_task(self.decode(state, socket))
        except (ExceptionGroup, DictationError, TimeoutError, ConnectionClosed):
            try:
                await socket.send(json.dumps({"event": "error"}))
            except ConnectionClosed:
                pass
        finally:
            self.active -= 1
            await socket.close()

    def health(self, socket: ServerConnection, request: Request) -> Response | None:
        if request.path == "/health":
            return socket.respond(
                HTTPStatus.OK,
                json.dumps(
                    {
                        "model": self.settings.gateway_model,
                        "mode": "utterance_incremental",
                        "active": self.active,
                        "maximum": self.settings.max_active_sessions,
                    }
                ),
            )
        return None


async def main() -> None:
    bridge = QwenBridge(DictationSettings())
    async with serve(
        bridge.handle, "0.0.0.0", 18014, max_size=32_000, max_queue=8, process_request=bridge.health
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
