"""Async stdio transport for the Codex app-server JSONL protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from harness.runtime.codex_protocol import (
    CodexMessage,
    CodexMessageKind,
    CodexProtocolError,
    classify_codex_message,
)
from harness.runtime.hooks import SdkDiagnosticTail, redact_sdk_stderr

CODEX_JSONL_MAX_LINE_BYTES = 32 * 1024 * 1024
CODEX_REQUEST_TIMEOUT_SECONDS = 30.0
CODEX_SHUTDOWN_TIMEOUT_SECONDS = 3.0


class CodexConnectionClosedError(RuntimeError):
    """Raised when the app-server connection closes with work in flight."""


class CodexRpcRemoteError(RuntimeError):
    """Content-minimizing representation of an app-server RPC error."""

    def __init__(self, *, method: str, code: int, message: str) -> None:
        self.method = method
        self.code = code
        self.remote_message = redact_sdk_stderr(message)[:2_000]
        super().__init__(f"Codex app-server request failed: method={method} code={code}")


class AsyncJsonlWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...

    def is_closing(self) -> bool: ...


type _InboundQueueItem = CodexMessage | BaseException | None


class CodexJsonlClient:
    """Correlate JSON-RPC responses while streaming notifications and requests."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: AsyncJsonlWriter,
        *,
        request_timeout_seconds: float = CODEX_REQUEST_TIMEOUT_SECONDS,
        max_line_bytes: int = CODEX_JSONL_MAX_LINE_BYTES,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("Codex request timeout must be positive")
        if max_line_bytes <= 0:
            raise ValueError("Codex JSONL line limit must be positive")
        self._reader = reader
        self._writer = writer
        self._request_timeout_seconds = request_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._next_request_id = 0
        self._pending: dict[int | str, tuple[str, asyncio.Future[object]]] = {}
        self._inbound: asyncio.Queue[_InboundQueueItem] = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._failure: BaseException | None = None
        self._closing = False

    def start(self) -> None:
        if self._reader_task is not None:
            raise RuntimeError("Codex JSONL client is already started")
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="codex-app-server-jsonl-reader"
        )

    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        if not method:
            raise ValueError("Codex RPC method must be non-empty")
        self._ensure_usable()
        self._next_request_id += 1
        request_id = self._next_request_id
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, future)
        payload: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        try:
            await self._write(payload)
            budget = timeout_seconds or self._request_timeout_seconds
            if budget <= 0:
                raise ValueError("Codex RPC timeout must be positive")
            return await asyncio.wait_for(asyncio.shield(future), timeout=budget)
        except BaseException:
            pending = self._pending.pop(request_id, None)
            if pending is not None and not pending[1].done():
                pending[1].cancel()
            raise

    async def notify(
        self, method: str, params: Mapping[str, object] | None = None
    ) -> None:
        if not method:
            raise ValueError("Codex notification method must be non-empty")
        self._ensure_usable()
        payload: dict[str, object] = {"method": method}
        if params is not None:
            payload["params"] = dict(params)
        await self._write(payload)

    async def respond(self, request_id: int | str, result: object) -> None:
        self._ensure_usable()
        await self._write({"id": request_id, "result": result})

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
    ) -> None:
        self._ensure_usable()
        await self._write(
            {
                "id": request_id,
                "error": {"code": code, "message": message[:2_000]},
            }
        )

    async def inbound(self) -> AsyncIterator[CodexMessage]:
        if self._reader_task is None:
            raise RuntimeError("Codex JSONL client is not started")
        while True:
            item = await self._inbound.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if not self._writer.is_closing():
            self._writer.close()
            with suppress(ConnectionError, RuntimeError):
                await self._writer.wait_closed()
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        self._fail_pending(CodexConnectionClosedError("Codex app-server client closed"))
        await self._inbound.put(None)

    def _ensure_usable(self) -> None:
        if self._reader_task is None:
            raise RuntimeError("Codex JSONL client is not started")
        if self._closing:
            raise CodexConnectionClosedError("Codex app-server client is closing")
        if self._failure is not None:
            raise CodexConnectionClosedError(
                "Codex app-server connection failed"
            ) from self._failure
        if self._reader_task.done():
            raise CodexConnectionClosedError("Codex app-server connection is closed")

    async def _write(self, payload: Mapping[str, object]) -> None:
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as error:
            raise CodexProtocolError("Codex RPC payload is not valid JSON") from error
        if len(encoded) > self._max_line_bytes:
            raise CodexProtocolError("Codex RPC payload exceeds the JSONL line limit")
        async with self._write_lock:
            self._writer.write(encoded)
            await self._writer.drain()

    async def _read_loop(self) -> None:
        terminal_error: BaseException | None = None
        try:
            while line := await self._reader.readline():
                if len(line) > self._max_line_bytes:
                    raise CodexProtocolError("app-server JSONL line exceeds the configured limit")
                try:
                    raw = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise CodexProtocolError("app-server emitted invalid JSONL") from error
                message = classify_codex_message(raw)
                if message.kind in {CodexMessageKind.RESPONSE, CodexMessageKind.ERROR}:
                    self._resolve_response(message)
                else:
                    await self._inbound.put(message)
            if not self._closing:
                terminal_error = CodexConnectionClosedError(
                    "Codex app-server closed stdout unexpectedly"
                )
        except asyncio.CancelledError:
            if not self._closing:
                terminal_error = CodexConnectionClosedError(
                    "Codex app-server reader was cancelled"
                )
            raise
        except BaseException as error:
            terminal_error = error
        finally:
            if terminal_error is not None:
                self._failure = terminal_error
                self._fail_pending(terminal_error)
                await self._inbound.put(terminal_error)
            await self._inbound.put(None)

    def _resolve_response(self, message: CodexMessage) -> None:
        request_id = message.payload.get("id")
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            raise CodexProtocolError("app-server response id is invalid")
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        method, future = pending
        if future.done():
            return
        if message.kind == CodexMessageKind.RESPONSE:
            future.set_result(message.payload.get("result"))
            return
        error = message.payload.get("error")
        typed_error = cast(dict[str, Any], error) if isinstance(error, dict) else {}
        code = typed_error.get("code")
        future.set_exception(
            CodexRpcRemoteError(
                method=method,
                code=(code if isinstance(code, int) and not isinstance(code, bool) else -32603),
                message=(
                    str(typed_error.get("message", "remote error"))
                    if typed_error
                    else "remote error"
                ),
            )
        )

    def _fail_pending(self, error: BaseException) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for _method, future in pending:
            if not future.done():
                future.set_exception(error)


@dataclass(frozen=True)
class CodexAppServerOptions:
    codex_path: Path
    working_directory: Path
    environment: Mapping[str, str] | None = None
    request_timeout_seconds: float = CODEX_REQUEST_TIMEOUT_SECONDS
    shutdown_timeout_seconds: float = CODEX_SHUTDOWN_TIMEOUT_SECONDS
    max_line_bytes: int = CODEX_JSONL_MAX_LINE_BYTES
    client_name: str = "axeno_agent_studio"
    client_title: str = "AXENO Agent Studio"
    client_version: str = "0.1.0"


class CodexAppServerProcess:
    """Own one app-server subprocess and its initialized JSONL connection."""

    def __init__(
        self,
        options: CodexAppServerOptions,
        *,
        stderr_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.options = options
        self.stderr_callback = stderr_callback or SdkDiagnosticTail()
        self.process: asyncio.subprocess.Process | None = None
        self.client: CodexJsonlClient | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> Mapping[str, object]:
        if self.process is not None:
            raise RuntimeError("Codex app-server process is already started")
        process = await asyncio.create_subprocess_exec(
            str(self.options.codex_path),
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.options.working_directory,
            env=(dict(self.options.environment) if self.options.environment is not None else None),
            limit=self.options.max_line_bytes + 1,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            await process.wait()
            raise RuntimeError("Codex app-server stdio pipes are unavailable")
        self.process = process
        self.client = CodexJsonlClient(
            process.stdout,
            process.stdin,
            request_timeout_seconds=self.options.request_timeout_seconds,
            max_line_bytes=self.options.max_line_bytes,
        )
        self.client.start()
        self._stderr_task = asyncio.create_task(
            self._read_stderr(process.stderr), name="codex-app-server-stderr-reader"
        )
        result = await self.client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self.options.client_name,
                    "title": self.options.client_title,
                    "version": self.options.client_version,
                }
            },
        )
        await self.client.notify("initialized", {})
        return cast(Mapping[str, object], result) if isinstance(result, dict) else {}

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        process = self.process
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=self.options.shutdown_timeout_seconds
                )
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task

    async def __aenter__(self) -> CodexAppServerProcess:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def _read_stderr(self, stderr: asyncio.StreamReader) -> None:
        while line := await stderr.readline():
            self.stderr_callback(line.decode("utf-8", errors="replace"))
