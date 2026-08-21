"""AgentRuntime implementation backed by the Codex app-server protocol."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from harness.runtime.base import (
    RuntimeContext,
    RuntimeEvent,
    RuntimeExecutionTimeoutError,
    RuntimeResultError,
)
from harness.runtime.codex_app_server import (
    CodexAppServerOptions,
    CodexAppServerProcess,
)
from harness.runtime.codex_protocol import (
    CodexMessage,
    CodexMessageKind,
    map_codex_notification,
)


class CodexRpcConnection(Protocol):
    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object: ...

    async def respond(self, request_id: int | str, result: object) -> None: ...

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
    ) -> None: ...

    def inbound(self) -> AsyncIterator[CodexMessage]: ...


class CodexProcess(Protocol):
    @property
    def client(self) -> CodexRpcConnection | None: ...

    async def start(self) -> Mapping[str, object]: ...

    async def close(self) -> None: ...


type CodexProcessFactory = Callable[[CodexAppServerOptions], CodexProcess]
type CodexServerRequestHandler = Callable[
    [RuntimeContext, CodexMessage], Awaitable[object]
]


def _default_process_factory(options: CodexAppServerOptions) -> CodexProcess:
    return CodexAppServerProcess(options)


@dataclass(frozen=True)
class CodexRuntimeConfig:
    codex_path: Path
    model: str | None = None
    model_provider: str | None = None
    developer_instructions: str | None = None
    environment: Mapping[str, str] | None = None
    approval_policy: str = "never"
    sandbox_mode: str = "workspace-write"
    network_access: bool = False
    turn_timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.turn_timeout_seconds is not None and self.turn_timeout_seconds <= 0:
            raise ValueError("Codex turn timeout must be positive")
        if self.approval_policy not in {"untrusted", "on-request", "never"}:
            raise ValueError("unsupported Codex approval policy")
        if self.sandbox_mode not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError("unsupported Codex sandbox mode")


class CodexAppServerRuntime:
    """Execute one Harness run as one Codex turn with durable thread resume."""

    def __init__(
        self,
        config: CodexRuntimeConfig,
        *,
        process_factory: CodexProcessFactory = _default_process_factory,
        server_request_handler: CodexServerRequestHandler | None = None,
    ) -> None:
        self._config = config
        self._process_factory = process_factory
        self._server_request_handler = server_request_handler

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        if context.session.runtime_type != "codex-app-server":
            raise ValueError("Codex runtime received a non-Codex session")
        try:
            if self._config.turn_timeout_seconds is None:
                async for event in self._execute(context):
                    yield event
                return
            async with asyncio.timeout(self._config.turn_timeout_seconds):
                async for event in self._execute(context):
                    yield event
        except TimeoutError as error:
            raise RuntimeExecutionTimeoutError("Codex turn timed out") from error

    async def _execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        process = self._process_factory(
            CodexAppServerOptions(
                codex_path=self._config.codex_path,
                working_directory=context.workspace,
                environment=self._config.environment,
            )
        )
        try:
            await process.start()
            client = process.client
            if client is None:
                raise RuntimeError("Codex app-server client is unavailable")
            thread_id = await self._open_thread(client, context)
            yield RuntimeEvent(
                type="runtime.thread.started",
                payload={"thread_id": thread_id, "runtime": "codex-app-server"},
            )
            await client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "text",
                            "text": self._turn_prompt(context),
                        }
                    ],
                    "approvalPolicy": self._config.approval_policy,
                    "sandboxPolicy": self._sandbox_policy(context.workspace),
                },
            )
            completed = False
            async for message in client.inbound():
                if message.kind is CodexMessageKind.SERVER_REQUEST:
                    await self._handle_server_request(client, context, message)
                    continue
                if message.kind is not CodexMessageKind.NOTIFICATION:
                    continue
                events = map_codex_notification(message.payload)
                for event in events:
                    if (
                        event.type == "runtime.thread.started"
                        and event.payload.get("thread_id") == thread_id
                    ):
                        continue
                    yield event
                    if event.type == "runtime.turn.completed":
                        completed = True
                        status = str(event.payload.get("status", "completed"))
                        if status != "completed":
                            raise RuntimeResultError(
                                f"codex_turn_{status}",
                                error_code="codex_turn_failed",
                                user_message="Codex 未能完成本轮任务，请重试。",
                            )
                if completed:
                    return
            raise RuntimeResultError(
                "codex_stream_closed",
                error_code="codex_connection_closed",
                user_message="Codex 连接意外中断，请重试。",
            )
        finally:
            await process.close()

    async def _open_thread(
        self,
        client: CodexRpcConnection,
        context: RuntimeContext,
    ) -> str:
        existing_thread_id = context.session.resolved_runtime_thread_id
        common: dict[str, object] = {
            "cwd": str(context.workspace),
            "approvalPolicy": self._config.approval_policy,
            "sandbox": self._config.sandbox_mode,
        }
        if self._config.model:
            common["model"] = self._config.model
        if self._config.model_provider:
            common["modelProvider"] = self._config.model_provider
        if self._config.developer_instructions:
            common["developerInstructions"] = self._config.developer_instructions
        if existing_thread_id:
            response = await client.request(
                "thread/resume",
                {**common, "threadId": existing_thread_id},
            )
        else:
            response = await client.request("thread/start", common)
        thread_id = self._thread_id(response)
        if existing_thread_id and thread_id != existing_thread_id:
            raise RuntimeError("Codex resumed a different thread than requested")
        return thread_id

    def _sandbox_policy(self, workspace: Path) -> dict[str, object]:
        if self._config.sandbox_mode == "danger-full-access":
            return {"type": "dangerFullAccess"}
        if self._config.sandbox_mode == "read-only":
            return {
                "type": "readOnly",
                "networkAccess": self._config.network_access,
            }
        return {
            "type": "workspaceWrite",
            "writableRoots": [str(workspace)],
            "networkAccess": self._config.network_access,
        }

    @staticmethod
    def _turn_prompt(context: RuntimeContext) -> str:
        prompt = str(context.run.input.get("prompt", ""))
        projections = tuple(
            value.strip()
            for value in (context.memory_projection, context.context_projection)
            if value.strip()
        )
        return "\n\n".join((*projections, prompt)) if projections else prompt

    @staticmethod
    def _thread_id(response: object) -> str:
        result = cast(dict[str, Any], response) if isinstance(response, dict) else {}
        thread = result.get("thread")
        typed_thread = cast(dict[str, Any], thread) if isinstance(thread, dict) else {}
        thread_id = typed_thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("Codex thread response is missing thread.id")
        return thread_id

    async def _handle_server_request(
        self,
        client: CodexRpcConnection,
        context: RuntimeContext,
        message: CodexMessage,
    ) -> None:
        request_id = message.payload.get("id")
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            raise RuntimeError("Codex server request has an invalid id")
        method = message.payload.get("method")
        if self._server_request_handler is not None:
            result = await self._server_request_handler(context, message)
            await client.respond(request_id, result)
            return
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            await client.respond(request_id, {"decision": "decline"})
            return
        await client.respond_error(
            request_id,
            code=-32601,
            message="unsupported server request",
        )
