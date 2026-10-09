"""Bounded, identity-scoped HTTP/SSE bridge to the official Nano WS service.

The web BFF keeps authentication cookies private. PCM arrives in ordered HTTP
batches; responses stream over SSE. Deployment requires one API process because
live WebSocket sessions are owned by that process, never by a task worker.
"""

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from secrets import token_urlsafe
from typing import cast

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from harness.dictation.preview import RealtimePreview
from harness.dictation.service import DictationError, DictationSettings


@dataclass
class RealtimeSession:
    session_id: str
    owner: tuple[str, str]
    socket: ClientConnection
    events: asyncio.Queue[dict[str, object]] = field(default_factory=lambda: asyncio.Queue(32))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    reader: asyncio.Task[None] | None = None
    watchdog: asyncio.Task[None] | None = None
    sequence: int = 0
    audio_bytes: int = 0
    text: str = ""
    preview: RealtimePreview = field(default_factory=RealtimePreview)
    finishing: bool = False
    subscribed: bool = False
    error: str = ""


class RealtimeDictation:
    def __init__(self, settings: DictationSettings) -> None:
        self.settings = settings
        self.sessions: dict[str, RealtimeSession] = {}
        self._creation = asyncio.Lock()

    async def start(self, owner: tuple[str, str]) -> RealtimeSession:
        async with self._creation:
            if len(self.sessions) >= self.settings.max_active_sessions:
                raise DictationError("语音服务繁忙，请稍后重试")
            if any(session.owner == owner for session in self.sessions.values()):
                raise DictationError("已有录音，请先结束或取消")
            socket = None
            try:
                socket = await connect(
                    self.settings.realtime_url,
                    proxy=None,
                    open_timeout=5,
                    close_timeout=2,
                    max_size=64_000,
                    max_queue=8,
                )
                await socket.send("START")
                response = json.loads(await asyncio.wait_for(socket.recv(), 5))
                if response.get("event") != "started":
                    raise DictationError("实时语音服务未确认开始录音")
            except (WebSocketException, OSError, TimeoutError, ValueError, AttributeError) as error:
                if socket is not None:
                    await socket.close()
                raise DictationError("实时语音服务暂时不可用") from error
            except DictationError:
                if socket is not None:
                    await socket.close()
                raise
            session = RealtimeSession(token_urlsafe(24), owner, socket)
            self.sessions[session.session_id] = session
            session.reader = asyncio.create_task(self._read(session))
            session.watchdog = asyncio.create_task(self._expire(session))
            return session

    def get(self, session_id: str, owner: tuple[str, str]) -> RealtimeSession:
        session = self.sessions.get(session_id)
        if session is None or session.owner != owner:
            raise DictationError("录音会话不存在或已结束")
        return session

    async def send(self, session: RealtimeSession, sequence: int, content: bytes) -> None:
        if not content or len(content) % 2 or len(content) > 32_000:
            raise DictationError("录音数据需要使用不超过一秒的 PCM16 片段")
        async with session.lock:
            if session.finishing or session.stopped.is_set():
                raise DictationError("录音已结束")
            if sequence != session.sequence:
                raise DictationError("录音片段顺序不一致，请重新录音")
            if session.audio_bytes + len(content) > self.settings.max_session_seconds * 32_000:
                raise DictationError("录音已达到时长上限")
            try:
                await asyncio.wait_for(session.socket.send(content), 5)
            except (WebSocketException, OSError, TimeoutError) as error:
                raise DictationError("实时语音连接已中断") from error
            session.audio_bytes += len(content)
            session.sequence += 1

    async def finish(self, session: RealtimeSession) -> str:
        async with session.lock:
            if not session.finishing and not session.stopped.is_set():
                session.finishing = True
                try:
                    await asyncio.wait_for(session.socket.send("STOP"), 5)
                except (WebSocketException, OSError, TimeoutError) as error:
                    raise DictationError("实时语音连接已中断") from error
        try:
            await asyncio.wait_for(session.stopped.wait(), 20)
        except TimeoutError as error:
            raise DictationError("语音尾部处理超时，可保留已显示的草稿") from error
        if session.error:
            raise DictationError(session.error)
        return session.text

    async def _read(self, session: RealtimeSession) -> None:
        try:
            async for raw in session.socket:
                payload: object = json.loads(raw)
                if not isinstance(payload, dict):
                    raise DictationError("实时语音服务返回了无效结果")
                result = cast(dict[str, object], payload)
                if result.get("event") == "stopped":
                    if not session.finishing:
                        raise DictationError("实时语音会话意外结束")
                    await session.events.put({"type": "done", "text": session.text})
                    break
                if result.get("event") == "error":
                    raise DictationError("实时语音服务处理失败")
                if "sentences" not in result:
                    continue
                sentences = result["sentences"]
                partial = result.get("partial", "")
                if not isinstance(sentences, list) or not isinstance(partial, str):
                    raise DictationError("实时语音服务返回了无效文字")
                # Official responses contain the complete locked sentence list.
                # Replace the preview, rather than append each partial again.
                pieces: list[str] = []
                for sentence in cast(list[object], sentences):
                    if not isinstance(sentence, dict):
                        raise DictationError("实时语音服务返回了无效句子")
                    sentence_text = cast(dict[str, object], sentence).get("text")
                    if not isinstance(sentence_text, str):
                        raise DictationError("实时语音服务返回了无效句子")
                    pieces.append(sentence_text)
                start = result.get("partial_start_ms")
                start_ms = start if type(start) is int and start >= 0 else None
                text = session.preview.update(
                    pieces, partial, start_ms, final=result.get("is_final") is True,
                )
                if len(text) > 12_000:
                    raise DictationError("语音文字过长，请分次输入")
                if text != session.text:
                    session.text = text
                    await session.events.put({"type": "draft", "text": text})
            else:
                raise DictationError("实时语音连接已中断，可保留已显示的草稿")
        except (WebSocketException, OSError, ValueError, DictationError) as error:
            session.error = (
                str(error) if isinstance(error, DictationError) else "实时语音连接已中断"
            )
            # Don't block cleanup when a disconnected consumer left a full queue.
            if session.events.full():
                session.events.get_nowait()
            session.events.put_nowait({"type": "error", "message": session.error})
        finally:
            session.stopped.set()
            await session.socket.close()

    async def _expire(self, session: RealtimeSession) -> None:
        await asyncio.sleep(self.settings.max_session_seconds + 30)
        await self.cancel(session.session_id)

    async def cancel(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        for task in (session.reader, session.watchdog):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        session.stopped.set()
        await session.socket.close()

    async def close(self) -> None:
        for session_id in list(self.sessions):
            await self.cancel(session_id)
