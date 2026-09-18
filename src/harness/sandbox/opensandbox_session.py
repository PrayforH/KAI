"""execd WebSocket PTY sessions: the bidirectional channel a remote CLI needs.

execd's request/response planes (``POST /command``, the file API) run one
command and return its output. A remote Claude or Codex CLI is a process that
stays open while the platform streams input into it, so this module drives
execd's PTY WebSocket instead: pipe mode separates stdout and stderr into
binary frames, stdin is sent as raw frames, and the shell's exit status arrives
as a JSON ``exit`` frame.

The WebSocket always goes through the Lifecycle API's execd proxy, so callers
need the same single reachable endpoint the rest of the provider uses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx

_STDIN_FRAME = b"\x00"
_STDOUT_FRAME = 0x01
_STDERR_FRAME = 0x02
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MCP_CONFIG_ENV = "HARNESS_CLAUDE_MCP_CONFIG"


class OpenSandboxSessionError(RuntimeError):
    """The PTY session could not be established or ended unexpectedly."""


class _Socket(Protocol):
    async def send(self, value: bytes | str) -> None: ...

    async def close(self) -> None: ...

    def __aiter__(self) -> Any: ...


class OpenSandboxPtySession:
    """One PTY-backed shell that speaks the harness RemoteClaudeSession contract."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        sandbox_id: str,
        execd_port: int,
        connector: Callable[..., Awaitable[_Socket]] | None = None,
        close_timeout: float = 5.0,
    ) -> None:
        if close_timeout <= 0:
            raise ValueError("PTY close timeout must be positive")
        self._client = client
        self._sandbox_id = sandbox_id
        self._execd_port = execd_port
        self._connector = connector
        self._close_timeout = close_timeout
        self._session_id: str | None = None
        self._socket: _Socket | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stdout: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stderr: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._exited = asyncio.Event()
        self._connected = asyncio.Event()
        self._exit_code: int | None = None
        self._terminated = False
        self._closed = False
        self._socket_failure: BaseException | None = None

    # -- execd addressing -------------------------------------------------

    def _proxy(self, path: str) -> str:
        return f"/v1/sandboxes/{self._sandbox_id}/proxy/{self._execd_port}{path}"

    def _socket_url(self, session_id: str) -> str:
        """Build the PTY WebSocket URL from the Lifecycle client's base URL.

        ``httpx.URL.netloc`` is bytes, so the authority is rebuilt from the host
        and port, which also keeps an explicit port for non-default endpoints.
        """

        base = self._client.base_url
        secure = base.scheme == "https"
        scheme = "wss" if secure else "ws"
        host = base.host or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = base.port or (443 if secure else 80)
        return (
            f"{scheme}://{host}:{port}{self._proxy(f'/pty/{session_id}/ws')}?pty=0"
        )

    # -- staging ----------------------------------------------------------

    async def _upload(self, remote_path: str, content: bytes, mode: int) -> None:
        files = [
            (
                "metadata",
                (
                    "metadata",
                    json.dumps({"path": remote_path, "mode": mode}),
                    "application/json",
                ),
            ),
            ("file", (remote_path, content, "application/octet-stream")),
        ]
        response = await self._client.post(self._proxy("/files/upload"), files=files)
        if response.status_code >= 400:
            raise OpenSandboxSessionError(
                f"OpenSandbox failed to stage {remote_path}: HTTP {response.status_code}"
            )

    async def stage_config(self, remote_directory: str, files: dict[str, bytes]) -> None:
        """Replace a remote config directory with the given files.

        The directory keeps mode 0700 and every file mode 0600: these files
        carry session credentials for the resumed conversation.
        """

        if not remote_directory.startswith("/"):
            raise ValueError("remote config directory must be absolute")
        await self._directories({remote_directory: {"mode": 700}})
        for relative, content in files.items():
            parts = [part for part in relative.split("/") if part]
            if not parts or ".." in parts:
                raise ValueError("remote config path escaped config directory")
            await self._upload(f"{remote_directory}/{'/'.join(parts)}", content, 600)

    async def _directories(self, entries: Mapping[str, Any]) -> None:
        response = await self._client.post(
            self._proxy("/directories"), json=dict(entries)
        )
        if response.status_code >= 400:
            raise OpenSandboxSessionError(
                f"OpenSandbox failed to create directories: HTTP {response.status_code}"
            )

    # -- session lifecycle ------------------------------------------------

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        if not argv:
            raise ValueError("remote command argv must not be empty")
        if self._socket is not None:
            raise OpenSandboxSessionError("remote PTY session is already started")
        mcp_config_path = await self._stage_mcp_config(env)
        session_id = await self._create_pty(cwd)
        self._session_id = session_id
        self._socket = await self._connect(session_id)
        self._reader = asyncio.create_task(self._read_frames())
        await self._await_connected()
        await self.write(self._command_line(argv, cwd, env, mcp_config_path))

    async def _create_pty(self, cwd: str) -> str:
        response = await self._client.post(self._proxy("/pty"), json={"cwd": cwd})
        if response.status_code >= 400:
            raise OpenSandboxSessionError(
                f"OpenSandbox failed to open a PTY session: HTTP {response.status_code}"
            )
        session_id = str(response.json().get("session_id", ""))
        if not session_id:
            raise OpenSandboxSessionError("OpenSandbox returned no PTY session id")
        return session_id

    async def _connect(self, session_id: str) -> _Socket:
        connector = self._connector
        if connector is None:
            import websockets

            connector = websockets.connect
        headers = {
            key: value
            for key, value in self._client.headers.items()
            if key.lower().startswith("open-sandbox")
        }
        socket = await connector(
            self._socket_url(session_id),
            additional_headers=headers,
            open_timeout=self._close_timeout,
        )
        return socket

    async def _await_connected(self) -> None:
        """Wait for execd's `connected` frame, which the reader consumes.

        The frame is handled by the reader task rather than peeked here so no
        output frame can be dropped while the handshake completes.
        """

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10)
        except TimeoutError as error:
            raise OpenSandboxSessionError(
                "OpenSandbox PTY session did not report itself connected"
            ) from error
        if self._closed:
            raise OpenSandboxSessionError(
                "OpenSandbox PTY session closed during its handshake"
            )

    async def _stage_mcp_config(self, env: Mapping[str, str]) -> str | None:
        """Materialize an inline MCP config as a remote file.

        Passing the JSON through the shell would embed a large quoted document
        in one command line; writing it with the file API keeps the command
        line small and the credentials file mode 0600.
        """

        payload = env.get(_MCP_CONFIG_ENV)
        if not payload:
            return None
        config_dir = env.get("CLAUDE_CONFIG_DIR") or "/tmp"
        path = f"{config_dir.rstrip('/')}/harness-mcp-{id(self):x}.json"
        await self._directories({config_dir: {"mode": 700}})
        await self._upload(path, payload.encode("utf-8"), 600)
        return path

    def _command_line(
        self,
        argv: list[str],
        cwd: str,
        env: Mapping[str, str],
        mcp_config_path: str | None,
    ) -> str:
        """Build one shell line that exports the environment and execs argv.

        Environment names are validated and values are quoted, so neither can
        change the shape of the command line.
        """

        steps = ["set -eu", f"cd {shlex.quote(cwd)}"]
        command = list(argv)
        for key, value in env.items():
            if key == _MCP_CONFIG_ENV:
                continue
            if not _ENV_NAME.fullmatch(key):
                raise ValueError(f"invalid remote environment name: {key}")
            steps.append(f"export {key}={shlex.quote(value)}")
        if mcp_config_path is not None:
            command = [*command, "--mcp-config", mcp_config_path]
        steps.append(f"exec {shlex.join(command)}")
        return " ; ".join(steps) + "\n"

    # -- frames -----------------------------------------------------------

    async def _read_frames(self) -> None:
        assert self._socket is not None
        try:
            async for frame in self._socket:
                if isinstance(frame, str):
                    await self._handle_text(frame)
                elif frame:
                    self._handle_binary(bytes(frame))
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # noqa: BLE001 - surfaced through wait()
            self._socket_failure = error
        finally:
            await self._finish()

    def _handle_binary(self, frame: bytes) -> None:
        if self._closed:
            return
        marker, payload = frame[0], frame[1:]
        if marker == _STDOUT_FRAME:
            self._stdout.put_nowait(payload)
        elif marker == _STDERR_FRAME:
            self._stderr.put_nowait(payload)
        # 0x03 replay frames belong to viewer attachments and carry no stdin
        # output for this session.

    async def _handle_text(self, frame: str) -> None:
        try:
            event = json.loads(frame)
        except json.JSONDecodeError:
            return
        if not isinstance(event, Mapping):
            return
        event_type = event.get("type")
        if event_type == "connected":
            self._connected.set()
            return
        if event_type != "exit":
            return
        try:
            self._exit_code = int(event.get("exit_code", 1))
        except (TypeError, ValueError):
            self._exit_code = 1
        # The shell is gone once execd reports its status, so end the streams
        # here rather than waiting for the socket to close: a server that keeps
        # the socket open must not leave readers waiting for output that cannot
        # arrive.
        await self._finish()

    async def _finish(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._exit_code is None:
            # A session we tore down ourselves is not a CLI failure; anything
            # else that ends without an exit frame is reported as one.
            self._exit_code = 0 if self._terminated else 1
        self._stdout.put_nowait(None)
        self._stderr.put_nowait(None)
        self._exited.set()

    async def write(self, data: str) -> None:
        if self._socket is None:
            raise OpenSandboxSessionError("remote PTY session is not started")
        await self._socket.send(_STDIN_FRAME + data.encode("utf-8"))

    async def end_input(self) -> None:
        """No-op: a WebSocket attachment has no half-close to send.

        The remote CLI ends when it finishes or when ``terminate`` signals it,
        so callers must not wait for stdin EOF to mean the turn is over.
        """

    async def read_stdout(self) -> bytes | None:
        return await self._stdout.get()

    async def read_stderr(self) -> bytes | None:
        return await self._stderr.get()

    async def wait(self) -> int:
        if not self._closed:
            await self._exited.wait()
        return self._exit_code if self._exit_code is not None else 1

    async def terminate(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        if self._socket is not None and self._exit_code is None:
            with contextlib.suppress(Exception):
                await self._socket.send(json.dumps({"type": "signal", "signal": "SIGINT"}))
                await asyncio.wait_for(self._exited.wait(), timeout=self._close_timeout)
        with contextlib.suppress(Exception):
            if self._socket is not None:
                await self._socket.close()
        if self._reader is not None and not self._reader.done():
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.shield(self._reader), timeout=self._close_timeout
                )
        if self._session_id is not None:
            with contextlib.suppress(Exception):
                await self._client.delete(self._proxy(f"/pty/{self._session_id}"))
        await self._finish()
