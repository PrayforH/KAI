"""Bounded, disposable Session-scoped SDK connections.

Only Worker-local CLIs with proxied file tools participate. The durable Session
gate and transcript store remain authoritative; no prompt is retried here.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import tempfile
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage
from mcp import types as mcp_types
from mcp.server import Server

from harness.runtime.ownership import ExecutionOwnership, execution_ownership


@dataclass
class RunBinding:
    options: ClaudeAgentOptions
    context: contextvars.Context
    authority: ExecutionOwnership
    messages: asyncio.Queue[object]
    complete: bool = False
    active: bool = True


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in cast(dict[Any, Any], value).items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in cast(list[Any] | tuple[Any, ...], value)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported mutable SDK option")


async def options_fingerprint(options: ClaudeAgentOptions, scope: str) -> str:
    """Hash values (including credentials), never log or expose them."""
    excluded = {"cwd", "resume", "hooks", "mcp_servers", "session_store", "stderr", "debug_stderr"}
    values = {
        field.name: _json_value(getattr(options, field.name))
        for field in fields(options)
        if field.name not in excluded
    }
    values["env"].pop("CLAUDE_CONFIG_DIR", None)
    values["scope"] = scope
    values["hooks"] = {
        event: [
            {"matcher": item.matcher, "timeout": item.timeout, "count": len(item.hooks)}
            for item in matchers
        ]
        for event, matchers in (options.hooks or {}).items()
    }
    servers: dict[str, Any] = {}
    if not isinstance(options.mcp_servers, dict):
        raise TypeError("Warm SDK requires explicit MCP definitions")
    for name, config in options.mcp_servers.items():
        if config.get("type") != "sdk":
            servers[name] = _json_value(config)
            continue
        server = cast(Any, config)["instance"]
        supported = {mcp_types.ListToolsRequest, mcp_types.CallToolRequest, mcp_types.PingRequest}
        if set(server.request_handlers) - supported:
            raise TypeError("Warm MCP proxy supports tool servers only")
        listing = await server.request_handlers[mcp_types.ListToolsRequest](
            mcp_types.ListToolsRequest(method="tools/list")
        )
        servers[name] = {
            "name": cast(Any, config)["name"],
            "tools": listing.model_dump(mode="json"),
        }
    values["mcp_servers"] = servers
    raw = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class _BoundStore:
    def __init__(self, entry: WarmEntry) -> None:
        self.entry = entry

    def __getattr__(self, name: str) -> Any:
        binding = self.entry.binding
        if name.startswith("_") or (
            binding is not None and not hasattr(binding.options.session_store, name)
        ):
            raise AttributeError(name)

        async def invoke(*args: Any, **kwargs: Any) -> Any:
            return await self.entry.dispatch(
                lambda binding: getattr(binding.options.session_store, name)(*args, **kwargs),
                store_flush=True,
            )

        return invoke


class WarmEntry:
    """One connection, one permanent response reader, one active Run binding."""

    def __init__(
        self,
        fingerprint: str,
        client_factory: Callable[..., Any],
    ) -> None:
        self.fingerprint = fingerprint
        self.directory = tempfile.TemporaryDirectory(prefix="harness-sdk-session-")
        self.workspace = Path(self.directory.name)
        self.client_factory = client_factory
        self.client: Any = None
        self.binding: RunBinding | None = None
        self.reader: asyncio.Task[None] | None = None
        self.callbacks: set[asyncio.Task[Any]] = set()
        self.healthy = True
        self.closed = False
        self.native_id: str | None = None
        self.revision: str | None = None
        self.idle_since = time.monotonic()
        self.reused = False
        self.miss_reason = "not_cached"

    async def dispatch(
        self, call: Callable[[RunBinding], Awaitable[Any]], *, store_flush: bool = False
    ) -> Any:
        binding = self.binding
        closing_store = store_flush and self.closed
        if binding is None or (not closing_store and (not binding.active or self.closed)):
            self.healthy = False
            raise RuntimeError("SDK callback has no active Run")
        # Preserve MCP's request context, but replace every captured Run context
        # (identity, memory, knowledge, artifacts, telemetry, ownership).
        callback_context = contextvars.copy_context()
        for variable, value in binding.context.items():
            callback_context.run(variable.set, value)

        async def execute() -> Any:
            await binding.authority.check()
            if self.binding is not binding or (not binding.active and not closing_store):
                raise RuntimeError("SDK callback belongs to a completed Run")
            return await call(binding)

        task = asyncio.create_task(execute(), context=callback_context)
        self.callbacks.add(task)
        try:
            return await task
        finally:
            self.callbacks.discard(task)

    def _hooks(self, options: ClaudeAgentOptions) -> Any:
        def callback(event: Any, index: int, hook_index: int) -> Any:
            async def invoke(*args: Any) -> Any:
                return await self.dispatch(
                    lambda binding: cast(Any, binding.options.hooks)[event][index].hooks[
                        hook_index
                    ](*args)
                )

            return invoke

        return {
            event: [
                replace(item, hooks=[callback(event, i, j) for j in range(len(item.hooks))])
                for i, item in enumerate(matchers)
            ]
            for event, matchers in (options.hooks or {}).items()
        }

    def _servers(self, options: ClaudeAgentOptions) -> Any:
        servers = dict(cast(dict[str, Any], options.mcp_servers))
        for name, config in list(servers.items()):
            if config.get("type") != "sdk":
                continue
            original = config["instance"]
            proxy: Any = Server(config["name"])

            def handler(server_name: str, request_type: Any) -> Any:
                async def invoke(request: Any) -> Any:
                    return await self.dispatch(
                        lambda binding: cast(Any, binding.options.mcp_servers)[server_name][
                            "instance"
                        ].request_handlers[request_type](request)
                    )

                return invoke

            for request_type in original.request_handlers:
                proxy.request_handlers[request_type] = handler(name, request_type)
            servers[name] = {**config, "instance": proxy}
        return servers

    async def connect(self, prepare: Callable[[Path], object]) -> None:
        assert self.binding is not None
        options = self.binding.options
        prepare(self.workspace)
        config_dir = self.workspace / ".runtime-config"
        config_dir.mkdir(mode=0o700)

        def stderr(line: str) -> None:
            binding = self.binding
            if binding is not None and binding.active and binding.options.stderr is not None:
                binding.options.stderr(line)

        stable_options = replace(
            options,
            cwd=self.workspace,
            env={**options.env, "CLAUDE_CONFIG_DIR": str(config_dir)},
            hooks=self._hooks(options),
            mcp_servers=self._servers(options),
            session_store=cast(Any, _BoundStore(self)),
            stderr=stderr,
        )
        self.client = self.client_factory(options=stable_options)
        # The SDK's permanent reader must not inherit the first Run's identity.
        await asyncio.create_task(self.client.connect(), context=contextvars.Context())
        self.reader = asyncio.create_task(self._read(), context=contextvars.Context())

    async def _read(self) -> None:
        try:
            async for message in self.client.receive_messages():
                binding = self.binding
                if binding is None or not binding.active:
                    self.healthy = False
                    return
                if type(message).__name__.startswith("Task"):
                    # First rollout does not retain sessions after delegated tasks.
                    self.healthy = False
                if getattr(message, "subtype", "") in {"mirror_error", "session_reset"}:
                    self.healthy = False
                if isinstance(message, ResultMessage):
                    self.native_id = message.session_id
                    if message.is_error:
                        self.healthy = False
                await binding.messages.put(message)
            self.healthy = False
            if self.binding is not None:
                await self.binding.messages.put(RuntimeError("SDK response stream closed"))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.healthy = False
            if self.binding is not None:
                await self.binding.messages.put(error)

    async def query(self, prompt: Any) -> None:
        assert self.binding is not None
        await self.binding.authority.check()
        await self.client.query(prompt)

    async def get_context_usage(self) -> Any:
        try:
            # The SDK public method requests full per-category token counting,
            # which makes additional provider API calls after text has ended.
            # The pinned CLI supports a summary from last-response usage and
            # local estimates; keep the control round trip as a drain barrier.
            query = getattr(self.client, "_query", None)
            request = getattr(query, "_send_control_request", None)
            if callable(request):
                return await cast(Callable[..., Awaitable[Any]], request)(
                    {"subtype": "get_context_usage", "detail": "summary"}
                )
            return await self.client.get_context_usage()
        except BaseException:
            self.healthy = False
            raise

    async def receive_messages(self) -> AsyncIterator[Any]:
        assert self.binding is not None
        binding = self.binding
        while True:
            message = await binding.messages.get()
            binding.authority.check_current()
            if isinstance(message, Exception):
                raise message
            yield message

    async def receive_response(self) -> AsyncIterator[Any]:
        async for message in self.receive_messages():
            yield message
            if isinstance(message, ResultMessage):
                return

    async def finish(self, result: ResultMessage) -> None:
        assert self.binding is not None
        if result.is_error or result.stop_reason != "end_turn":
            return
        await self.binding.authority.check()
        # The SDK flushes before yielding result, but the CLI can emit trailing
        # mirror frames during the post-result control request. Drain them while
        # this Run still owns its lease, before taking the history watermark.
        # Fail closed if a future SDK changes this private adapter boundary.
        query = getattr(self.client, "_query", None)
        batcher = getattr(query, "_transcript_mirror_batcher", None)
        if batcher is None or not callable(getattr(batcher, "flush", None)):
            self.healthy = False
            return
        # Eager mirroring schedules detached flush tasks. A direct flush can
        # drain the buffer before those tasks have started; wait for the last
        # scheduled one while this Run still owns its binding and lease.
        await batcher.flush()
        eager_flush = getattr(batcher, "_flush_task", None)
        if eager_flush is not None:
            await eager_flush.wait()
            await batcher.flush()
        if self.callbacks:
            self.healthy = False
            return
        self.revision = await cast(Any, self.binding.options.session_store).revision()
        self.binding.complete = True

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.healthy = False
        if self.binding is not None:
            self.binding.active = False
        if self.reader is not None:
            self.reader.cancel()
            with suppress(asyncio.CancelledError):
                await self.reader
        callbacks = list(self.callbacks)
        for task in callbacks:
            task.cancel()
        if callbacks:
            await asyncio.gather(*callbacks, return_exceptions=True)
        try:
            if self.client is not None:
                await self.client.disconnect()
        finally:
            self.binding = None
            self.directory.cleanup()


class WarmSdkPool:
    def __init__(
        self,
        *,
        idle_seconds: float = 300,
        max_sessions: int = 16,
        client_factory: Callable[..., Any] = ClaudeSDKClient,
    ) -> None:
        if idle_seconds <= 0 or max_sessions < 1:
            raise ValueError("Warm SDK limits must be positive")
        self.idle_seconds = idle_seconds
        self.max_sessions = max_sessions
        self.client_factory = client_factory
        self.entries: dict[tuple[str, str, str], WarmEntry] = {}
        self.lock = asyncio.Lock()
        self.reaper: asyncio.Task[None] | None = None
        self.closed = False

    @staticmethod
    def eligible(options: ClaudeAgentOptions) -> bool:
        # Only immutable Skill assets may be read on the Worker; all business
        # filesystem/shell work must go through freshly bound sandbox MCP tools.
        if not isinstance(options.tools, list) or set(options.tools) - {"Skill", "Task", "Agent"}:
            return False
        for agent in (options.agents or {}).values():
            if agent.background or any(
                name not in {"Skill", "Task", "Agent"} and not name.startswith("mcp__")
                for name in (agent.tools or [])
            ):
                return False
        return (
            execution_ownership.get() is not None
            and options.can_use_tool is None
            and not options.setting_sources
            and bool(options.hooks)
            and callable(getattr(options.session_store, "revision", None))
        )

    @asynccontextmanager
    async def acquire(
        self,
        key: tuple[str, str, str],
        options: ClaudeAgentOptions,
        *,
        scope: str,
        prepare: Callable[[Path], object],
    ) -> AsyncGenerator[WarmEntry | None]:
        if self.closed or not self.eligible(options):
            yield None
            return
        try:
            fingerprint = await options_fingerprint(options, scope)
        except (TypeError, KeyError):
            yield None
            return
        authority = execution_ownership.get()
        assert authority is not None
        await authority.check()
        revision = await cast(Any, options.session_store).revision()
        binding = RunBinding(options, contextvars.copy_context(), authority, asyncio.Queue(256))
        entry: WarmEntry | None = None
        async with self.lock:
            old = self.entries.get(key)
            if old is not None and old.binding is not None:
                # Never bypass Session serialization through a second client.
                raise RuntimeError("Concurrent warm SDK acquisition for one Session")
            miss_reason = "not_cached"
            if old is not None:
                miss_reason = next((reason for changed, reason in (
                    (not old.healthy, "unhealthy"),
                    (old.fingerprint != fingerprint, "options_changed"),
                    (old.native_id != options.resume, "native_session_changed"),
                    (old.revision != revision, "history_changed"),
                    (time.monotonic() - old.idle_since >= self.idle_seconds, "idle_expired"),
                ) if changed), "")
            if old is not None and miss_reason:
                await old.close()
                del self.entries[key]
                old = None
            if old is None and len(self.entries) >= self.max_sessions:
                idle = [(k, v) for k, v in self.entries.items() if v.binding is None]
                if idle:
                    victim_key, victim = min(idle, key=lambda pair: pair[1].idle_since)
                    await victim.close()
                    del self.entries[victim_key]
            if old is not None:
                entry = old
                entry.reused = True
            elif len(self.entries) < self.max_sessions:
                entry = WarmEntry(fingerprint, self.client_factory)
                entry.miss_reason = miss_reason
                self.entries[key] = entry
            if entry is not None:
                entry.binding = binding
            if self.reaper is None:
                self.reaper = asyncio.create_task(self._reap(), context=contextvars.Context())
        if entry is None:
            yield None
            return
        try:
            if entry.client is None:
                await entry.connect(prepare)
            yield entry
        except BaseException as error:
            if not isinstance(error, GeneratorExit):
                entry.healthy = False
            raise
        finally:
            binding.active = False
            keep = (
                entry.healthy
                and binding.complete
                and binding.messages.empty()
                and not entry.callbacks
            )
            entry.idle_since = time.monotonic()
            if not keep:
                # Retain ONLY the revoked binding for the SDK's final store
                # flush. Tools are disabled; loss of gate authority still
                # rejects writes. Ordinary cancellation preserves its mirror.
                await entry.close()
                async with self.lock:
                    if self.entries.get(key) is entry:
                        del self.entries[key]
            else:
                entry.binding = None

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(min(self.idle_seconds, 10))
            async with self.lock:
                for key, entry in list(self.entries.items()):
                    if entry.binding is None and (
                        not entry.healthy
                        or time.monotonic() - entry.idle_since >= self.idle_seconds
                    ):
                        await entry.close()
                        del self.entries[key]

    async def close(self) -> None:
        self.closed = True
        if self.reaper is not None:
            self.reaper.cancel()
            with suppress(asyncio.CancelledError):
                await self.reaper
        await asyncio.gather(*(entry.close() for entry in self.entries.values()))
        self.entries.clear()


@dataclass(frozen=True)
class WarmSdkRequest:
    pool: WarmSdkPool
    key: tuple[str, str, str]
    scope: str
    prepare: Callable[[Path], object]
