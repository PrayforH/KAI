"""Claude Agent SDK transport that runs the Claude CLI inside Daytona."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

import anyio
from claude_agent_sdk import ClaudeAgentOptions, Transport
from claude_agent_sdk._internal.session_resume import materialize_resume_session
from claude_agent_sdk._version import __version__ as claude_agent_sdk_version

from harness.runtime.audit_redaction import redact_text

logger = logging.getLogger(__name__)
_DIAGNOSTIC_TAIL_BYTES = 16 * 1024


class DaytonaTransportError(RuntimeError):
    """Remote Claude process failed without forwarding sensitive diagnostics."""


class RemoteClaudeSession(Protocol):
    async def stage_config(
        self, remote_directory: str, files: dict[str, bytes]
    ) -> None: ...

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None: ...

    async def write(self, data: str) -> None: ...

    async def end_input(self) -> None: ...

    async def read_stdout(self) -> bytes | None: ...

    async def read_stderr(self) -> bytes | None: ...

    async def wait(self) -> int: ...

    async def terminate(self) -> None: ...


def _mcp_config(options: ClaudeAgentOptions) -> str | None:
    if not options.mcp_servers:
        return None
    if isinstance(options.mcp_servers, (str, Path)):
        return str(options.mcp_servers)
    servers: dict[str, object] = {}
    for name, raw in options.mcp_servers.items():
        if raw.get("type") == "sdk":
            raise DaytonaTransportError(
                "in-process SDK MCP servers cannot be serialized into Daytona; "
                f"configure an authenticated HTTP MCP server instead ({name})"
            )
        servers[name] = raw
    return json.dumps({"mcpServers": servers}, separators=(",", ":"))


def build_remote_claude_command(
    options: ClaudeAgentOptions, *, cli_path: str
) -> list[str]:
    if not cli_path or any(character.isspace() for character in cli_path):
        raise ValueError("an absolute Claude CLI path is required")
    command = [
        cli_path,
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if isinstance(options.system_prompt, str):
        command.extend(["--system-prompt", options.system_prompt])
    if isinstance(options.tools, list):
        command.extend(["--tools", ",".join(options.tools)])
    if options.allowed_tools:
        command.extend(["--allowedTools", ",".join(options.allowed_tools)])
    if options.max_turns:
        command.extend(["--max-turns", str(options.max_turns)])
    if options.model:
        command.extend(["--model", options.model])
    if options.permission_mode:
        command.extend(["--permission-mode", options.permission_mode])
    if options.resume:
        command.extend(["--resume", options.resume])
    if options.session_store is not None:
        command.append("--session-mirror")
    if options.include_partial_messages:
        command.append("--include-partial-messages")
    if options.strict_mcp_config:
        command.append("--strict-mcp-config")
    if options.skills is not None:
        command.append("--setting-sources=user,project")
    command.extend(["--input-format", "stream-json"])
    return command


class DaytonaClaudeTransport(Transport):
    def __init__(
        self,
        *,
        session: RemoteClaudeSession,
        options: ClaudeAgentOptions,
        remote_workspace: str,
        cli_path: str,
        close_timeout: float = 5.0,
    ) -> None:
        self._session = session
        self._options = options
        self._remote_workspace = remote_workspace
        self._cli_path = cli_path
        self._close_timeout = close_timeout
        self._ready = False
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()
        self._stdout_tail = bytearray()
        self._stdout_chunk_count = 0
        self._stdout_message_count = 0

    def _diagnostic_text(self, raw: bytes) -> str:
        text = raw.decode("utf-8", errors="replace")
        for key, value in self._options.env.items():
            upper = key.upper()
            if value and any(
                marker in upper
                for marker in ("TOKEN", "KEY", "SECRET", "PASSWORD", "AUTH")
            ):
                text = text.replace(value, "[REDACTED]")
                text = text.replace(
                    base64.b64encode(value.encode("utf-8")).decode("ascii"),
                    "[REDACTED]",
                )
        return redact_text(text, limit=4_000)

    async def _stage_resume_config(self) -> None:
        if self._options.session_store is None or self._options.resume is None:
            return
        remote_config_dir = self._options.env.get("CLAUDE_CONFIG_DIR")
        if not remote_config_dir or not remote_config_dir.startswith("/"):
            raise DaytonaTransportError(
                "remote session resume requires an absolute CLAUDE_CONFIG_DIR"
            )
        materialized = await materialize_resume_session(
            replace(self._options, cwd=Path(self._remote_workspace))
        )
        if materialized is None:
            return
        try:
            files = {
                path.relative_to(materialized.config_dir).as_posix(): path.read_bytes()
                for path in materialized.config_dir.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            await self._session.stage_config(remote_config_dir, files)
        finally:
            await materialized.cleanup()

    async def connect(self) -> None:
        if self._ready:
            return
        await self._stage_resume_config()
        environment = {
            **self._options.env,
            "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
            "CLAUDE_AGENT_SDK_VERSION": claude_agent_sdk_version,
            # Daytona's stdout demultiplexer retains a short suffix while it
            # waits to rule out a stream marker. The remote wrapper adds a
            # harmless padding line after each CLI protocol line so the JSON
            # newline is delivered immediately instead of after process exit.
            "HARNESS_DAYTONA_STDOUT_FLUSH_PADDING": "1",
        }
        mcp_config = _mcp_config(self._options)
        if mcp_config is not None:
            environment["HARNESS_CLAUDE_MCP_CONFIG"] = mcp_config
        await self._session.start(
            build_remote_claude_command(
                self._options, cli_path=self._cli_path
            ),
            self._remote_workspace,
            environment,
        )
        self._ready = True
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        while (chunk := await self._session.read_stderr()) is not None:
            self._stderr_tail.extend(chunk)
            if len(self._stderr_tail) > _DIAGNOSTIC_TAIL_BYTES:
                del self._stderr_tail[:-_DIAGNOSTIC_TAIL_BYTES]

    async def write(self, data: str) -> None:
        if not self._ready:
            raise DaytonaTransportError("remote Claude transport is not connected")
        await self._session.write(data)

    async def end_input(self) -> None:
        if self._ready:
            await self._session.end_input()

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        buffer = ""
        while True:
            chunk = await self._session.read_stdout()
            if chunk is None:
                break
            self._stdout_chunk_count += 1
            self._stdout_tail.extend(chunk)
            if len(self._stdout_tail) > _DIAGNOSTIC_TAIL_BYTES:
                del self._stdout_tail[:-_DIAGNOSTIC_TAIL_BYTES]
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                parsed = self._parse_line(line)
                if parsed is not None:
                    self._stdout_message_count += 1
                    yield parsed
            # Daytona can split one long stdout line across log callbacks and
            # withhold its trailing newline while the process remains active.
            # Control responses are self-delimiting JSON objects, so consume
            # complete prefixes without waiting for a newline that may arrive
            # only after the CLI exits.
            parsed_prefixes, buffer = self._parse_complete_prefixes(buffer)
            for parsed in parsed_prefixes:
                self._stdout_message_count += 1
                yield parsed
        parsed_prefixes, buffer = self._parse_complete_prefixes(buffer)
        for parsed in parsed_prefixes:
            self._stdout_message_count += 1
            yield parsed
        parsed = self._parse_line(buffer)
        if parsed is not None:
            self._stdout_message_count += 1
            yield parsed
        exit_code = await self._session.wait()
        if exit_code != 0:
            raise DaytonaTransportError(
                f"remote Claude CLI exited with code {exit_code}"
            )

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped:
            return None
        try:
            value: object = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return cast(dict[str, Any], value)

    @staticmethod
    def _parse_complete_prefixes(
        buffer: str,
    ) -> tuple[list[dict[str, Any]], str]:
        decoder = json.JSONDecoder()
        parsed: list[dict[str, Any]] = []
        remaining = buffer
        while remaining:
            leading = len(remaining) - len(remaining.lstrip())
            candidate = remaining[leading:]
            if not candidate:
                return parsed, ""
            try:
                value, end = decoder.raw_decode(candidate)
            except json.JSONDecodeError:
                break
            if isinstance(value, dict):
                parsed.append(cast(dict[str, Any], value))
            remaining = candidate[end:]
        return parsed, remaining

    async def close(self) -> None:
        self._ready = False
        with anyio.move_on_after(self._close_timeout):
            await self._session.terminate()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with anyio.move_on_after(self._close_timeout):
                try:
                    await self._stderr_task
                except asyncio.CancelledError:
                    pass
            self._stderr_task = None
        if self._stderr_tail:
            logger.warning(
                "remote Claude CLI stderr before close: %s",
                self._diagnostic_text(bytes(self._stderr_tail)),
            )
        if self._stdout_message_count == 0:
            logger.warning(
                "remote Claude CLI closed without parsed stdout messages "
                "(stdout_chunks=%s, stdout_tail=%s)",
                self._stdout_chunk_count,
                self._diagnostic_text(bytes(self._stdout_tail)),
            )

    def is_ready(self) -> bool:
        return self._ready
