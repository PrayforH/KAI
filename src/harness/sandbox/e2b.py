"""E2B SandboxProvider and Claude CLI transport adapters."""

from __future__ import annotations

import asyncio
import shlex
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

from claude_agent_sdk import ClaudeAgentOptions
from e2b import AsyncSandbox, CommandExitException, NotFoundException

from harness.core.models import Run
from harness.runtime.codex_app_server import CodexAppServerOptions, DaytonaCodexAppServerProcess
from harness.runtime.daytona_transport import DaytonaClaudeTransport, RemoteClaudeSession
from harness.sandbox.base import SandboxCommandResult, SandboxHandle, SandboxIsolation


class E2BRemoteSandbox(Protocol):
    id: str

    async def ensure_claude_cli(self, *, version: str, path: str) -> None: ...

    async def create_folder(self, path: str) -> None: ...

    async def upload(self, remote_path: str, content: bytes) -> None: ...

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]: ...

    async def download(self, remote_path: str) -> bytes: ...

    async def kill(self) -> None: ...

    def remote_session(self) -> RemoteClaudeSession: ...


class E2BClient(Protocol):
    async def create(
        self,
        *,
        template: str,
        timeout: int,
        allow_internet_access: bool,
        metadata: Mapping[str, str],
    ) -> E2BRemoteSandbox: ...


class _WritableFilesystem(Protocol):
    async def make_dir(self, path: str) -> object: ...

    async def write(self, path: str, data: bytes) -> object: ...


class SdkE2BRemoteSession:
    """Adapt an E2B background command to the Claude SDK Transport contract."""

    def __init__(self, sandbox: AsyncSandbox) -> None:
        self._sandbox = sandbox
        self._process: Any | None = None
        self._wait_task: asyncio.Task[None] | None = None
        self._exit_code: int | None = None
        self._stdout: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stderr: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def stage_config(self, remote_directory: str, files: dict[str, bytes]) -> None:
        filesystem = cast(_WritableFilesystem, self._sandbox.files)
        await self._sandbox.commands.run(
            f"rm -rf -- {shlex.quote(remote_directory)} && "
            f"mkdir -p -- {shlex.quote(remote_directory)}"
        )
        for relative, content in files.items():
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("remote Claude config path escaped config directory")
            remote_path = str(PurePosixPath(remote_directory) / path)
            await filesystem.make_dir(str(PurePosixPath(remote_path).parent))
            await filesystem.write(remote_path, content)

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        if not argv:
            raise ValueError("remote command argv must not be empty")
        command = (
            "set -eu; mcp_config_path=''; "
            'if [ -n "${HARNESS_CLAUDE_MCP_CONFIG:-}" ]; then '
            'mcp_config_path="$(mktemp)"; chmod 600 "$mcp_config_path"; '
            'printf "%s" "$HARNESS_CLAUDE_MCP_CONFIG" > "$mcp_config_path"; '
            "unset HARNESS_CLAUDE_MCP_CONFIG; "
            f'set -- {shlex.join(argv)} --mcp-config "$mcp_config_path"; '
            f"else set -- {shlex.join(argv)}; fi; "
            'trap \'[ -z "$mcp_config_path" ] || '
            'rm -f -- "$mcp_config_path"\' EXIT; '
            'exec "$@"'
        )

        async def stdout(value: str) -> None:
            await self._stdout.put(value.encode("utf-8"))

        async def stderr(value: str) -> None:
            await self._stderr.put(value.encode("utf-8"))

        self._process = await self._sandbox.commands.run(
            command,
            background=True,
            stdin=True,
            envs=env,
            cwd=cwd,
            on_stdout=stdout,
            on_stderr=stderr,
            timeout=0,
        )
        self._wait_task = asyncio.create_task(self._wait_for_exit())

    async def _wait_for_exit(self) -> None:
        assert self._process is not None
        try:
            result = await self._process.wait()
            self._exit_code = int(result.exit_code)
        except CommandExitException as error:
            self._exit_code = error.exit_code
        finally:
            await self._stdout.put(None)
            await self._stderr.put(None)

    async def write(self, data: str) -> None:
        if self._process is None:
            raise RuntimeError("remote E2B command is not started")
        await self._process.send_stdin(data)

    async def end_input(self) -> None:
        if self._process is not None:
            try:
                await self._process.close_stdin()
            except NotFoundException:
                # Short diagnostic commands can exit before the controller
                # receives close_stdin. Their wait result remains authoritative.
                pass

    async def read_stdout(self) -> bytes | None:
        return await self._stdout.get()

    async def read_stderr(self) -> bytes | None:
        return await self._stderr.get()

    async def wait(self) -> int:
        if self._wait_task is not None:
            # Cancelling a caller must not cancel the process watcher: terminate
            # still needs it to observe the remote kill and drain both streams.
            await asyncio.shield(self._wait_task)
        return self._exit_code if self._exit_code is not None else 1

    async def terminate(self) -> None:
        if self._process is not None and self._exit_code is None:
            try:
                await self._process.kill()
            except NotFoundException:
                pass
        if self._wait_task is not None and not self._wait_task.cancelled():
            try:
                await asyncio.wait_for(asyncio.shield(self._wait_task), timeout=5)
            except TimeoutError:
                self._wait_task.cancel()
                await asyncio.gather(self._wait_task, return_exceptions=True)


class SdkE2BRemoteSandbox:
    def __init__(self, sandbox: AsyncSandbox) -> None:
        self._sandbox = sandbox
        self.id = sandbox.sandbox_id

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        expected = f"{version} (Claude Code)"
        try:
            check = await self._sandbox.commands.run(f"{shlex.quote(path)} --version")
        except Exception:  # noqa: BLE001 - a missing CLI is an expected cache miss
            check = None
        if check is not None and check.exit_code == 0 and check.stdout.strip() == expected:
            return
        installer = (
            "set -o pipefail; "
            "curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors "
            "https://claude.ai/install.sh | bash -s "
            f"{shlex.quote(version)}"
        )
        try:
            installed = await self._sandbox.commands.run(installer, timeout=180)
        except Exception as error:
            raise RuntimeError("failed to install the pinned Claude CLI in E2B") from error
        if installed.exit_code != 0:
            raise RuntimeError("failed to install the pinned Claude CLI in E2B")
        try:
            verified = await self._sandbox.commands.run(f"{shlex.quote(path)} --version")
        except Exception as error:
            raise RuntimeError("E2B Claude CLI version verification failed") from error
        if verified.exit_code != 0 or verified.stdout.strip() != expected:
            raise RuntimeError("E2B Claude CLI version verification failed")

    async def create_folder(self, path: str) -> None:
        await self._sandbox.files.make_dir(path)

    async def upload(self, remote_path: str, content: bytes) -> None:
        filesystem = cast(_WritableFilesystem, self._sandbox.files)
        await filesystem.write(remote_path, content)

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]:
        entries = await self._sandbox.files.list(remote_path, depth=100)
        return [
            (
                str(entry.path),
                getattr(entry.type, "value", entry.type) == "dir",
                int(entry.size) if entry.size >= 0 else None,
            )
            for entry in entries
        ]

    async def download(self, remote_path: str) -> bytes:
        return cast(bytes, await self._sandbox.files.read(remote_path, format="bytes"))

    async def kill(self) -> None:
        await self._sandbox.kill()

    def remote_session(self) -> RemoteClaudeSession:
        return SdkE2BRemoteSession(self._sandbox)


class SdkE2BClient:
    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key

    async def create(
        self,
        *,
        template: str,
        timeout: int,
        allow_internet_access: bool,
        metadata: Mapping[str, str],
    ) -> E2BRemoteSandbox:
        sandbox = await AsyncSandbox.create(
            template=template,
            timeout=timeout,
            secure=True,
            allow_internet_access=allow_internet_access,
            metadata=dict(metadata),
            api_key=self._api_key,
        )
        return SdkE2BRemoteSandbox(sandbox)


class E2BSandboxProvider:
    """Run each Harness Run in a dedicated E2B sandbox."""

    def __init__(
        self,
        *,
        client: E2BClient,
        local_root: Path | None = None,
        template: str = "base",
        timeout_seconds: int = 3600,
        allow_internet_access: bool = True,
        remote_workspace_root: str = "/home/user/harness",
        cli_version: str = "2.1.206",
        cli_path: str = "/home/user/.local/bin/claude",
        max_collect_bytes: int = 512 * 1024 * 1024,
        max_collect_members: int = 10_000,
        provider_name: str = "e2b",
        codex_cli_path: str | None = None,
        codex_cli_version: str = "",
        codex_cli_sha256: str = "",
    ) -> None:
        if timeout_seconds <= 0 or max_collect_bytes <= 0 or max_collect_members <= 0:
            raise ValueError("E2B lifecycle and collection limits must be positive")
        self._client = client
        self.provider_name = provider_name
        self._codex_cli_path = codex_cli_path
        self._codex_cli_version = codex_cli_version
        self._codex_cli_sha256 = codex_cli_sha256
        self._local_root = local_root
        self._template = template
        self._timeout_seconds = timeout_seconds
        self._allow_internet_access = allow_internet_access
        self._remote_workspace_root = remote_workspace_root.rstrip("/")
        self._cli_version = cli_version
        self._cli_path = cli_path
        self._max_collect_bytes = max_collect_bytes
        self._max_collect_members = max_collect_members
        self._sandboxes: dict[str, E2BRemoteSandbox] = {}

    def remote_workspace_for(self, run: Run) -> str:
        return f"{self._remote_workspace_root}/{run.run_id}"

    async def provision(self, run: Run) -> SandboxHandle:
        sandbox = await self._client.create(
            template=self._template,
            timeout=self._timeout_seconds,
            allow_internet_access=self._allow_internet_access,
            metadata={
                "harness.tenant": run.tenant_id,
                "harness.session": run.session_id,
                "harness.run": run.run_id,
            },
        )
        self._sandboxes[sandbox.id] = sandbox
        path = Path(tempfile.mkdtemp(prefix=f"{run.run_id}-", dir=self._local_root))
        remote_workspace = self.remote_workspace_for(run)

        def transport_factory(raw_options: object) -> object:
            if isinstance(raw_options, CodexAppServerOptions):
                if self._codex_cli_path is None:
                    raise ValueError(f"{self.provider_name} Codex transport is not configured")
                bootstrap = getattr(sandbox, "ensure_codex_cli", None)

                async def prepare_codex() -> None:
                    if bootstrap is not None:
                        await cast(Callable[..., Awaitable[None]], bootstrap)(
                            version=self._codex_cli_version, path=self._codex_cli_path
                        )

                return DaytonaCodexAppServerProcess(
                    session=sandbox.remote_session(),
                    options=raw_options,
                    remote_workspace=remote_workspace,
                    cli_path=self._codex_cli_path,
                    cli_version=self._codex_cli_version,
                    cli_sha256=self._codex_cli_sha256,
                    prepare_cli=prepare_codex,
                )
            options = cast(ClaudeAgentOptions, raw_options)
            options.env = {
                **options.env,
                "CLAUDE_CONFIG_DIR": (
                    f"{self._remote_workspace_root}/.claude-config/{run.session_id}"
                ),
            }
            return DaytonaClaudeTransport(
                session=sandbox.remote_session(),
                options=options,
                remote_workspace=remote_workspace,
                cli_path=self._cli_path,
            )

        return SandboxHandle(
            sandbox_id=sandbox.id,
            path=path,
            provider=self.provider_name,
            isolation_level=SandboxIsolation.CONTAINER,
            remote_workspace=remote_workspace,
            runtime_transport_factory=transport_factory,
        )

    async def prepare(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes[handle.sandbox_id]
        assert handle.remote_workspace is not None
        if not handle.deferred_tool_execution:
            await sandbox.ensure_claude_cli(
                version=self._cli_version,
                path=self._cli_path,
            )
        await sandbox.create_folder(handle.remote_workspace)
        for path in sorted(handle.path.rglob("*")):
            relative = path.relative_to(handle.path).as_posix()
            remote = f"{handle.remote_workspace}/{relative}"
            if path.is_dir():
                await sandbox.create_folder(remote)
            elif path.is_file() and not path.is_symlink():
                await sandbox.upload(remote, path.read_bytes())

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        if not argv:
            raise ValueError("sandbox command argv must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("sandbox command timeout must be positive")
        if handle.remote_workspace is None:
            raise ValueError("E2B command requires a remote workspace")
        session = self._sandboxes[handle.sandbox_id].remote_session()
        await session.start(list(argv), handle.remote_workspace, dict(environment or {}))
        try:
            await session.end_input()

            async def read(stream: str) -> bytes:
                chunks: list[bytes] = []
                reader = session.read_stdout if stream == "stdout" else session.read_stderr
                while (chunk := await reader()) is not None:
                    chunks.append(chunk)
                return b"".join(chunks)

            stdout, stderr, exit_code = await asyncio.wait_for(
                asyncio.gather(read("stdout"), read("stderr"), session.wait()),
                timeout=timeout_seconds,
            )
        finally:
            await session.terminate()
        return SandboxCommandResult(
            exit_code=exit_code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def collect(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes[handle.sandbox_id]
        assert handle.remote_workspace is not None
        entries = await sandbox.list_files(handle.remote_workspace)
        if len(entries) > self._max_collect_members:
            raise ValueError("E2B workspace exceeds collection member limit")
        declared_size = sum(size for _, is_dir, size in entries if not is_dir and size is not None)
        if declared_size > self._max_collect_bytes:
            raise ValueError("E2B workspace exceeds collection size limit")
        collected_size = 0
        for remote_path, is_dir, _size in entries:
            relative = PurePosixPath(remote_path).relative_to(
                PurePosixPath(handle.remote_workspace)
            )
            if ".." in relative.parts:
                raise ValueError("E2B workspace path escaped local collection root")
            local = handle.path.joinpath(*relative.parts)
            if is_dir:
                local.mkdir(parents=True, exist_ok=True)
                continue
            content = await sandbox.download(remote_path)
            collected_size += len(content)
            if collected_size > self._max_collect_bytes:
                raise ValueError("E2B workspace exceeds collection size limit")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)

    async def destroy(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes.pop(handle.sandbox_id, None)
        try:
            if sandbox is not None:
                await sandbox.kill()
        finally:
            shutil.rmtree(handle.path, ignore_errors=True)
