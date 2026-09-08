"""AgentRuntime implementation backed by the Codex app-server protocol."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
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
    CodexRpcRemoteError,
    CodexRpcRequestTimeoutError,
)
from harness.runtime.codex_protocol import (
    CodexMessage,
    CodexMessageKind,
    CodexNotificationMapper,
)

_CODEX_ARCHIVED_HOME = ".codex-home"
_CODEX_HOME_MARKER = ".harness-native-home"
_CODEX_STABLE_HOME_ROOT = ".harness-codex-homes"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LocalCodexHome:
    native: Path
    archived: Path


def _stable_codex_home(execution_workspace: Path, context: RuntimeContext) -> Path:
    digest = hashlib.sha256(
        f"{context.session.tenant_id}\0{context.session.session_id}".encode()
    ).hexdigest()[:32]
    return execution_workspace.parent / _CODEX_STABLE_HOME_ROOT / digest


def _valid_codex_home(
    candidate: Path,
    *,
    execution_workspace: Path,
    stable: Path,
) -> bool:
    if not candidate.is_absolute():
        return False
    if candidate == stable:
        return True
    # Releases before the stable-HOME fix persisted the first Run's absolute
    # workspace path. Accept only that narrow local-sandbox shape so an
    # existing Session can resume without trusting an arbitrary rollout path.
    return (
        candidate.name == _CODEX_ARCHIVED_HOME
        and candidate.parent.name.startswith("run_")
        and candidate.parent.parent == execution_workspace.parent
    )


def _legacy_codex_home(
    archived: Path,
    *,
    thread_id: str,
    execution_workspace: Path,
    stable: Path,
) -> Path | None:
    sessions = archived / ".codex" / "sessions"
    if not sessions.is_dir():
        return None
    for rollout in sessions.rglob(f"*{thread_id}.jsonl"):
        try:
            with rollout.open(encoding="utf-8") as stream:
                record = json.loads(stream.readline())
            typed_record = cast(dict[str, Any], record) if isinstance(record, dict) else {}
            raw_payload = typed_record.get("payload", {})
            payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
            cwd = payload.get("cwd")
            candidate = Path(cwd) / _CODEX_ARCHIVED_HOME if isinstance(cwd, str) else None
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if candidate is not None and _valid_codex_home(
            candidate,
            execution_workspace=execution_workspace,
            stable=stable,
        ):
            return candidate
    return None


def _prepare_local_codex_home(
    execution_workspace: Path,
    context: RuntimeContext,
) -> _LocalCodexHome:
    archived = execution_workspace / _CODEX_ARCHIVED_HOME
    stable = _stable_codex_home(execution_workspace, context)
    native = stable
    marker = archived / _CODEX_HOME_MARKER
    if marker.is_file():
        try:
            candidate = Path(marker.read_text(encoding="utf-8").strip())
        except OSError:
            candidate = stable
        if _valid_codex_home(
            candidate,
            execution_workspace=execution_workspace,
            stable=stable,
        ):
            native = candidate
    elif context.session.resolved_runtime_thread_id is not None:
        native = (
            _legacy_codex_home(
                archived,
                thread_id=context.session.resolved_runtime_thread_id,
                execution_workspace=execution_workspace,
                stable=stable,
            )
            or stable
        )

    native.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if native.exists():
        shutil.rmtree(native)
    if archived.is_dir():
        shutil.copytree(archived, native)
    else:
        native.mkdir(mode=0o700)
    (native / _CODEX_HOME_MARKER).write_text(str(native), encoding="utf-8")
    return _LocalCodexHome(native=native, archived=archived)


def _persist_local_codex_home(home: _LocalCodexHome) -> None:
    if home.archived.exists():
        shutil.rmtree(home.archived)
    shutil.copytree(home.native, home.archived)
    if home.native != home.archived:
        shutil.rmtree(home.native)


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
type CodexServerRequestHandler = Callable[[RuntimeContext, CodexMessage], Awaitable[object]]


def _default_process_factory(options: CodexAppServerOptions) -> CodexProcess:
    return CodexAppServerProcess(options)


@dataclass(frozen=True)
class CodexRuntimeConfig:
    codex_path: Path
    model: str | None = None
    model_provider: str | None = None
    developer_instructions: str | None = None
    environment: Mapping[str, str] | None = None
    config_overrides: tuple[str, ...] = ()
    approval_policy: str = "never"
    sandbox_mode: str = "workspace-write"
    network_access: bool = False
    turn_timeout_seconds: float | None = None
    max_tool_calls: int | None = None

    def __post_init__(self) -> None:
        if self.turn_timeout_seconds is not None and self.turn_timeout_seconds <= 0:
            raise ValueError("Codex turn timeout must be positive")
        if self.max_tool_calls is not None and self.max_tool_calls <= 0:
            raise ValueError("Codex tool-call limit must be positive")
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
        except CodexRpcRequestTimeoutError as error:
            raise RuntimeResultError(
                "codex_control_request_timeout",
                error_code="codex_control_timeout",
                user_message=(
                    f"Codex 运行环境启动超时，请重试。尚未进入模型执行阶段（{error.method}）。"
                ),
            ) from error
        except CodexRpcRemoteError as error:
            # Keep the upstream diagnostic in service logs while exposing a
            # stable, actionable message to users. In particular, required MCP
            # discovery happens during thread/start, so an unavailable MCP
            # server otherwise appears as a generic runtime failure.
            logger.warning(
                "codex control request rejected method=%s code=%s remote_message=%s",
                error.method,
                error.code,
                error.remote_message,
            )
            if error.method == "thread/start":
                raise RuntimeResultError(
                    "codex_thread_start_failed",
                    error_code="codex_thread_start_failed",
                    user_message=(
                        "Codex 新对话启动失败：智能体依赖的 MCP 服务可能无响应。"
                        "请稍后重试；若持续失败，请检查 MCP 连接状态。"
                    ),
                ) from error
            if error.method == "thread/resume":
                raise RuntimeResultError(
                    "codex_thread_resume_failed",
                    error_code="codex_thread_resume_failed",
                    user_message="Codex 会话恢复失败，请新建对话后重试。",
                ) from error
            raise RuntimeResultError(
                "codex_control_request_failed",
                error_code="codex_control_request_failed",
                user_message="Codex 执行请求被运行时拒绝，请重试。",
            ) from error

    async def _execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        execution_workspace = (
            Path(context.remote_workspace)
            if context.runtime_transport_factory is not None and context.remote_workspace
            else context.workspace
        )
        environment = dict(self._config.environment or {})
        local_home: _LocalCodexHome | None = None
        if self._config.environment is not None:
            if context.runtime_transport_factory is None:
                local_home = await asyncio.to_thread(
                    _prepare_local_codex_home,
                    execution_workspace,
                    context,
                )
                environment["HOME"] = str(local_home.native)
            else:
                environment.setdefault("HOME", str(execution_workspace / _CODEX_ARCHIVED_HOME))
            environment.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        options = CodexAppServerOptions(
            codex_path=self._config.codex_path,
            working_directory=execution_workspace,
            environment=(environment if self._config.environment is not None else None),
            config_overrides=self._config.config_overrides,
        )
        process = (
            cast(CodexProcess, context.runtime_transport_factory(options))
            if context.runtime_transport_factory is not None
            else self._process_factory(options)
        )
        steering_task: asyncio.Task[None] | None = None
        try:
            await process.start()
            client = process.client
            if client is None:
                raise RuntimeError("Codex app-server client is unavailable")
            thread_id, recovered_thread_id = await self._open_thread(
                client, context, execution_workspace
            )
            if recovered_thread_id is not None:
                yield RuntimeEvent(
                    type="runtime.session.recovered",
                    payload={
                        "previous_session_id": recovered_thread_id,
                        "runtime": "codex-app-server",
                    },
                )
            yield RuntimeEvent(
                type="runtime.thread.started",
                payload={"thread_id": thread_id, "runtime": "codex-app-server"},
            )
            notification_mapper = CodexNotificationMapper(thread_id)
            turn_response = await client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {
                            "type": "text",
                            "text": self._turn_prompt(
                                context,
                                include_context_projection=(
                                    context.session.resolved_runtime_thread_id is None
                                    or recovered_thread_id is not None
                                ),
                            ),
                        }
                    ],
                    "approvalPolicy": self._config.approval_policy,
                    "sandboxPolicy": self._sandbox_policy(execution_workspace),
                },
            )
            turn_id = self._turn_id(turn_response)
            if context.steering is not None:
                await context.steering.open()

                async def deliver_guidance() -> None:
                    assert context.steering is not None
                    while True:
                        for item in await context.steering.read():
                            await context.steering.sending(item)
                            try:
                                await client.request(
                                    "turn/steer",
                                    {
                                        "threadId": thread_id,
                                        "expectedTurnId": turn_id,
                                        "input": [{"type": "text", "text": item.text}],
                                    },
                                )
                            except Exception:
                                await context.steering.acknowledge(
                                    item,
                                    error="当前回合已结束或无法接收引导，请在下一条消息中重试",
                                )
                            else:
                                await context.steering.acknowledge(item)
                        await asyncio.sleep(0.25)

                steering_task = asyncio.create_task(deliver_guidance())
            completed = False
            tool_call_count = 0
            last_runtime_error = "Other"
            async for message in client.inbound():
                if steering_task is not None and steering_task.done():
                    await steering_task
                if message.kind is CodexMessageKind.SERVER_REQUEST:
                    await self._handle_server_request(client, context, message)
                    continue
                if message.kind is not CodexMessageKind.NOTIFICATION:
                    continue
                events = notification_mapper.map(message.payload)
                for event in events:
                    if (
                        event.type == "runtime.thread.started"
                        and event.payload.get("thread_id") == thread_id
                    ):
                        continue
                    if event.type == "runtime.error":
                        last_runtime_error = str(event.payload.get("code") or "Other")
                    if event.type == "tool.request":
                        tool_call_count += 1
                        if (
                            self._config.max_tool_calls is not None
                            and tool_call_count > self._config.max_tool_calls
                        ):
                            await client.request(
                                "turn/interrupt",
                                {"threadId": thread_id, "turnId": turn_id},
                            )
                            yield RuntimeEvent(
                                type="runtime.error",
                                payload={
                                    "code": "ToolCallLimitExceeded",
                                    "runtime": "codex-app-server",
                                    "limit": self._config.max_tool_calls,
                                },
                            )
                            yield self._thread_invalidated(
                                thread_id,
                                "ToolCallLimitExceeded",
                            )
                            raise RuntimeResultError(
                                "codex_tool_call_limit",
                                error_code="codex_tool_call_limit",
                                user_message=(
                                    "本轮工具调用超过控制面上限，已停止重复执行。"
                                    "请缩小范围或补充更精确的筛选条件。"
                                ),
                            )
                    yield event
                    if event.type == "runtime.turn.completed":
                        completed = True
                        status = str(event.payload.get("status", "completed"))
                        if status != "completed":
                            yield self._thread_invalidated(thread_id, last_runtime_error)
                            raise RuntimeResultError(
                                f"codex_turn_{status}",
                                error_code=self._turn_error_code(last_runtime_error),
                                user_message=self._turn_error_message(last_runtime_error),
                            )
                if completed:
                    return
            raise RuntimeResultError(
                "codex_stream_closed",
                error_code="codex_connection_closed",
                user_message="Codex 连接意外中断，请重试。",
            )
        finally:
            try:
                if steering_task is not None:
                    steering_task.cancel()
                    try:
                        with suppress(asyncio.CancelledError):
                            await steering_task
                    finally:
                        assert context.steering is not None
                        await context.steering.close()
            finally:
                try:
                    await process.close()
                finally:
                    if local_home is not None:
                        await asyncio.to_thread(_persist_local_codex_home, local_home)

    async def _open_thread(
        self,
        client: CodexRpcConnection,
        context: RuntimeContext,
        execution_workspace: Path,
    ) -> tuple[str, str | None]:
        existing_thread_id = context.session.resolved_runtime_thread_id
        common: dict[str, object] = {
            "cwd": str(execution_workspace),
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
            try:
                response = await client.request(
                    "thread/resume",
                    {**common, "threadId": existing_thread_id},
                )
            except CodexRpcRemoteError as error:
                if not self._recoverable_resume_error(error):
                    raise
                # A restored Session may retain the Codex rollout while the
                # app-server rejects its durable thread metadata (for example
                # after the isolated Daytona workspace path changes). Start a
                # fresh native thread and let the Worker atomically replace the
                # stale binding before the turn begins.
                response = await client.request("thread/start", common)
                return self._thread_id(response), existing_thread_id
        else:
            response = await client.request("thread/start", common)
        thread_id = self._thread_id(response)
        if existing_thread_id and thread_id != existing_thread_id:
            raise RuntimeError("Codex resumed a different thread than requested")
        return thread_id, None

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
    def _turn_prompt(
        context: RuntimeContext,
        *,
        include_context_projection: bool = True,
    ) -> str:
        prompt = str(context.run.input.get("prompt", ""))
        history_projection = ""
        raw_history = context.run.input.get("conversation_prompts")
        if include_context_projection and isinstance(raw_history, list):
            prompts = [
                value.strip()
                for value in cast(list[object], raw_history)
                if isinstance(value, str) and value.strip()
            ]
            if prompts and prompts[-1] == prompt.strip():
                prompts = prompts[:-1]
            if prompts:
                bounded_prompts: list[str] = []
                remaining_chars = 18_000
                for value in reversed(prompts[-20:]):
                    clipped = value[: min(2_000, remaining_chars)]
                    if not clipped:
                        break
                    bounded_prompts.insert(0, clipped)
                    remaining_chars -= len(clipped)
                history_projection = (
                    '<conversation_recovery source="durable-user-turns">\n'
                    + json.dumps(bounded_prompts, ensure_ascii=False)
                    + "\n</conversation_recovery>"
                )
        projections = tuple(
            value.strip()
            for value in (
                context.memory_projection,
                (context.context_projection if include_context_projection else ""),
                history_projection,
            )
            if value.strip()
        )
        return "\n\n".join((*projections, prompt)) if projections else prompt

    @staticmethod
    def _recoverable_resume_error(error: CodexRpcRemoteError) -> bool:
        if error.method != "thread/resume":
            return False
        message = error.remote_message.lower()
        return (
            error.code in {-32602, -32001}
            and "thread" in message
            and any(
                hint in message
                for hint in ("not found", "unavailable", "missing", "unknown", "stale")
            )
        )

    @staticmethod
    def _thread_invalidated(thread_id: str, reason_code: str) -> RuntimeEvent:
        return RuntimeEvent(
            type="runtime.thread.invalidated",
            payload={
                "thread_id": thread_id,
                "runtime": "codex-app-server",
                "reason_code": reason_code,
            },
        )

    @staticmethod
    def _turn_error_code(reason_code: str) -> str:
        return {
            "ContextWindowExceeded": "codex_context_window_exceeded",
            "RateLimited": "codex_rate_limited",
            "AuthenticationFailed": "codex_authentication_failed",
            "ToolCallLimitExceeded": "codex_tool_call_limit",
        }.get(reason_code, "codex_turn_failed")

    @staticmethod
    def _turn_error_message(reason_code: str) -> str:
        return {
            "ContextWindowExceeded": (
                "Codex 上下文已达到模型窗口上限，平台已重置运行线程，请重试本条任务。"
            ),
            "RateLimited": "模型渠道正在限流，请稍后重试。",
            "AuthenticationFailed": "模型渠道认证失败，请检查控制面的模型渠道配置。",
            "ToolCallLimitExceeded": "本轮工具调用达到上限，请缩小范围后重试。",
        }.get(
            reason_code,
            "Codex 运行线程异常，平台已保留工作区并重置会话，请重试本条任务。",
        )

    @staticmethod
    def _thread_id(response: object) -> str:
        result = cast(dict[str, Any], response) if isinstance(response, dict) else {}
        thread = result.get("thread")
        typed_thread = cast(dict[str, Any], thread) if isinstance(thread, dict) else {}
        thread_id = typed_thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("Codex thread response is missing thread.id")
        return thread_id

    @staticmethod
    def _turn_id(response: object) -> str:
        result = cast(dict[str, Any], response) if isinstance(response, dict) else {}
        turn = result.get("turn")
        typed_turn = cast(dict[str, Any], turn) if isinstance(turn, dict) else {}
        turn_id = typed_turn.get("id")
        if not isinstance(turn_id, str) or not turn_id:
            raise RuntimeError("Codex turn response is missing turn.id")
        return turn_id

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
