"""Daytona SandboxProvider plus adapters for the official async SDK."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import os
import re
import shlex
import shutil
import ssl
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast
from uuid import uuid4

import certifi
from claude_agent_sdk import ClaudeAgentOptions
from daytona import (
    AsyncDaytona,
    CreateSandboxFromSnapshotParams,
    DaytonaConfig,
    DaytonaConflictError,
    DaytonaNotFoundError,
)
from daytona._async.sandbox import AsyncSandbox
from daytona.common.process import SessionExecuteRequest

from harness.core.models import Run
from harness.runtime.codex_app_server import (
    CodexAppServerOptions,
    DaytonaCodexAppServerProcess,
)
from harness.runtime.daytona_transport import (
    DaytonaClaudeTransport,
    RemoteClaudeSession,
)
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxHandle,
    SandboxIsolation,
)

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
logger = logging.getLogger(__name__)


def configure_default_ca_bundle() -> None:
    """Use certifi when this Python installation has no usable default CA file."""
    default_ca_file: object = getattr(ssl.get_default_verify_paths(), "cafile", None)
    default_ca_is_usable = isinstance(default_ca_file, str) and Path(default_ca_file).is_file()
    if "SSL_CERT_FILE" not in os.environ and not default_ca_is_usable:
        os.environ["SSL_CERT_FILE"] = certifi.where()


class DaytonaRemoteSandbox(Protocol):
    id: str

    async def healthcheck(self) -> bool: ...

    async def ensure_claude_cli(self, *, version: str, path: str) -> None: ...

    async def create_folder(self, path: str) -> None: ...

    async def remove_tree(self, path: str) -> None: ...

    async def upload(self, remote_path: str, content: bytes) -> None: ...

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]: ...

    async def download(self, remote_path: str) -> bytes: ...

    async def download_archive(self, remote_path: str, *, max_bytes: int) -> bytes: ...

    def remote_session(self) -> RemoteClaudeSession: ...


class DaytonaClient(Protocol):
    async def create(self, **parameters: Any) -> DaytonaRemoteSandbox: ...

    async def get(self, sandbox_id: str) -> DaytonaRemoteSandbox: ...

    async def start(self, sandbox: DaytonaRemoteSandbox) -> None: ...

    async def stop(self, sandbox: DaytonaRemoteSandbox) -> None: ...

    async def delete(self, sandbox: DaytonaRemoteSandbox) -> None: ...


class SdkDaytonaRemoteSession:
    def __init__(self, sandbox: AsyncSandbox) -> None:
        self._sandbox = sandbox
        self._session_id = f"harness-{uuid4().hex}"
        self._end_input_marker = f"__HARNESS_END_INPUT_{uuid4().hex}__"
        self._command_id: str | None = None
        self._stdout: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stderr: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._logs_task: asyncio.Task[None] | None = None

    async def stage_config(self, remote_directory: str, files: dict[str, bytes]) -> None:
        quoted_directory = shlex.quote(remote_directory)
        prepared = await self._sandbox.process.exec(
            f"rm -rf -- {quoted_directory} && mkdir -p -- {quoted_directory}"
        )
        if prepared.exit_code != 0:
            raise RuntimeError("failed to prepare remote Claude config directory")
        for relative, content in files.items():
            relative_path = PurePosixPath(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError("remote Claude config path escaped config directory")
            remote_path = str(PurePosixPath(remote_directory) / relative_path)
            parent = str(PurePosixPath(remote_path).parent)
            created = await self._sandbox.process.exec(f"mkdir -p -- {shlex.quote(parent)}")
            if created.exit_code != 0:
                raise RuntimeError("failed to create remote Claude config directory")
            await self._sandbox.fs.upload_file(content, remote_path)

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        if not argv:
            raise ValueError("remote command argv must not be empty")
        environment_lines: list[str] = []
        for key, value in sorted(env.items()):
            if _ENVIRONMENT_NAME.fullmatch(key) is None:
                raise ValueError(f"invalid remote environment variable name: {key}")
            encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
            environment_lines.append(f"{key}={encoded}")
        argument_lines = [
            base64.b64encode(argument.encode("utf-8")).decode("ascii") for argument in argv
        ]
        await self._sandbox.process.create_session(self._session_id)
        environment_marker = f"__HARNESS_END_ENV_{uuid4().hex}__"
        argument_marker = f"__HARNESS_END_ARGV_{uuid4().hex}__"
        stdin_wrapper = (
            'env_marker="$1"; arg_marker="$2"; input_marker="$3"; shift 3; '
            "while IFS= read -r env_line; do "
            '[ "$env_line" = "$env_marker" ] && break; '
            'key="${env_line%%=*}"; encoded="${env_line#*=}"; '
            'value="$(printf "%s" "$encoded" | base64 -d)" || exit 70; '
            'export "$key=$value"; '
            "done; "
            "while IFS= read -r encoded; do "
            '[ "$encoded" = "$arg_marker" ] && break; '
            'value="$(printf "%s" "$encoded" | base64 -d)" || exit 70; '
            'set -- "$@" "$value"; '
            "done; "
            '[ "$#" -gt 0 ] || exit 64; '
            'mcp_config_path=""; '
            'if [ -n "${HARNESS_CLAUDE_MCP_CONFIG:-}" ]; then '
            'mcp_config_path="$(mktemp)"; chmod 600 "$mcp_config_path"; '
            'printf "%s" "$HARNESS_CLAUDE_MCP_CONFIG" > "$mcp_config_path"; '
            "unset HARNESS_CLAUDE_MCP_CONFIG; "
            'set -- "$@" --mcp-config "$mcp_config_path"; '
            "fi; "
            'trap \'[ -z "$mcp_config_path" ] || '
            'rm -f -- "$mcp_config_path"\' EXIT; '
            "unset CLAUDECODE; "
            'stdout_flush_padding="${HARNESS_DAYTONA_STDOUT_FLUSH_PADDING:-}"; '
            "unset HARNESS_DAYTONA_STDOUT_FLUSH_PADDING; "
            "forward_stdin() { "
            "while IFS= read -r line; do "
            'if [ "$line" = "$input_marker" ]; then break; fi; '
            'printf "%s\\n" "$line"; '
            "done; "
            "}; "
            'if [ "$stdout_flush_padding" = "1" ]; then '
            'forward_stdin | "$@" | '
            'while IFS= read -r output_line || [ -n "$output_line" ]; do '
            'printf "%s\\n                \\n" "$output_line"; '
            "done; "
            "else "
            'forward_stdin | "$@"; '
            "fi"
        )
        wrapped_argv = [
            "bash",
            "-o",
            "pipefail",
            "-c",
            stdin_wrapper,
            "harness-stdin",
            environment_marker,
            argument_marker,
            self._end_input_marker,
        ]
        command = f"cd {shlex.quote(cwd)} && {shlex.join(wrapped_argv)}"
        response = await self._sandbox.process.execute_session_command(
            self._session_id,
            SessionExecuteRequest(
                command=command,
                run_async=True,
                suppress_input_echo=True,
            ),
        )
        self._command_id = response.cmd_id
        environment_lines.append(environment_marker)
        environment_lines.extend(argument_lines)
        environment_lines.append(argument_marker)
        await self._sandbox.process.send_session_command_input(
            self._session_id,
            self._command_id,
            "\n".join(environment_lines) + "\n",
        )
        self._logs_task = asyncio.create_task(self._stream_logs())

    async def _stream_logs(self) -> None:
        assert self._command_id is not None

        async def stdout(chunk: str) -> None:
            await self._stdout.put(chunk.encode("utf-8"))

        async def stderr(chunk: str) -> None:
            await self._stderr.put(chunk.encode("utf-8"))

        try:
            await self._sandbox.process.get_session_command_logs_async(
                self._session_id, self._command_id, stdout, stderr
            )
        finally:
            await self._stdout.put(None)
            await self._stderr.put(None)

    async def write(self, data: str) -> None:
        if self._command_id is None:
            raise RuntimeError("remote Claude command is not started")
        await self._sandbox.process.send_session_command_input(
            self._session_id, self._command_id, data
        )

    async def end_input(self) -> None:
        if self._command_id is not None:
            await self._sandbox.process.send_session_command_input(
                self._session_id,
                self._command_id,
                f"{self._end_input_marker}\n",
            )

    async def read_stdout(self) -> bytes | None:
        return await self._stdout.get()

    async def read_stderr(self) -> bytes | None:
        return await self._stderr.get()

    async def wait(self) -> int:
        if self._logs_task is not None:
            await self._logs_task
        if self._command_id is None:
            return 1
        command = await self._sandbox.process.get_session_command(
            self._session_id, self._command_id
        )
        return command.exit_code if command.exit_code is not None else 1

    async def terminate(self) -> None:
        await self._sandbox.process.delete_session(self._session_id)


class SdkDaytonaRemoteSandbox:
    def __init__(self, sandbox: AsyncSandbox) -> None:
        self._sandbox = sandbox
        self.id = sandbox.id

    async def healthcheck(self) -> bool:
        try:
            response = await self._sandbox.process.exec("true", timeout=10)
        except Exception:  # noqa: BLE001 - a stale remote is a cache miss
            return False
        return response.exit_code == 0

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        quoted_path = shlex.quote(path)
        expected = f"{version} (Claude Code)"
        check = await self._sandbox.process.exec(f"{quoted_path} --version")
        if check.exit_code == 0 and check.result.strip() == expected:
            return
        installer = (
            "set -o pipefail; "
            "curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors "
            "https://claude.ai/install.sh | bash -s "
            f"{shlex.quote(version)}"
        )
        installed = await self._sandbox.process.exec(
            f"bash -lc {shlex.quote(installer)}", timeout=180
        )
        if installed.exit_code != 0:
            raise RuntimeError("failed to install the pinned Claude CLI in Daytona")
        verified = await self._sandbox.process.exec(f"{quoted_path} --version")
        if verified.exit_code != 0 or verified.result.strip() != expected:
            raise RuntimeError("Daytona Claude CLI version verification failed")

    async def create_folder(self, path: str) -> None:
        response = await self._sandbox.process.exec(f"mkdir -p -- {shlex.quote(path)}")
        if response.exit_code != 0:
            raise RuntimeError("failed to create Daytona workspace directory")

    async def remove_tree(self, path: str) -> None:
        response = await self._sandbox.process.exec(f"rm -rf -- {shlex.quote(path)}")
        if response.exit_code != 0:
            raise RuntimeError("failed to clean Daytona workspace directory")

    async def upload(self, remote_path: str, content: bytes) -> None:
        await self._sandbox.fs.upload_file(content, remote_path)

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]:
        files = await self._sandbox.fs.list_files(remote_path, depth=100)
        return [
            (
                file.path or str(PurePosixPath(remote_path) / file.name),
                file.is_dir,
                file.size if file.size >= 0 else None,
            )
            for file in files
        ]

    async def download(self, remote_path: str) -> bytes:
        return await self._sandbox.fs.download_file(remote_path)

    async def download_archive(self, remote_path: str, *, max_bytes: int) -> bytes:
        archive_path = f"/tmp/harness-workspace-{uuid4().hex}.tar"
        try:
            response = await self._sandbox.process.exec(
                f"tar -C {shlex.quote(remote_path)} -cf {shlex.quote(archive_path)} ."
            )
            if response.exit_code != 0:
                raise RuntimeError("failed to archive Daytona workspace")
            content = await self._sandbox.fs.download_file(archive_path)
            if len(content) > max_bytes:
                raise ValueError("Daytona workspace archive exceeds collection size limit")
            return content
        finally:
            await self._sandbox.process.exec(f"rm -f -- {shlex.quote(archive_path)}")

    def remote_session(self) -> RemoteClaudeSession:
        return SdkDaytonaRemoteSession(self._sandbox)

    @property
    def sdk_sandbox(self) -> AsyncSandbox:
        return self._sandbox


class SdkDaytonaClient:
    def __init__(self, sdk: AsyncDaytona) -> None:
        self._sdk = sdk

    @classmethod
    def from_config(
        cls, *, api_key: str, api_url: str | None = None, target: str | None = None
    ) -> SdkDaytonaClient:
        configure_default_ca_bundle()
        return cls(AsyncDaytona(DaytonaConfig(api_key=api_key, api_url=api_url, target=target)))

    async def create(self, **parameters: Any) -> DaytonaRemoteSandbox:
        sandbox = await self._sdk.create(CreateSandboxFromSnapshotParams.model_validate(parameters))
        return SdkDaytonaRemoteSandbox(sandbox)

    async def get(self, sandbox_id: str) -> DaytonaRemoteSandbox:
        return SdkDaytonaRemoteSandbox(await self._sdk.get(sandbox_id))

    async def start(self, sandbox: DaytonaRemoteSandbox) -> None:
        sdk_sandbox = cast(SdkDaytonaRemoteSandbox, sandbox).sdk_sandbox
        state = getattr(sdk_sandbox.state, "value", sdk_sandbox.state)
        if state != "started":
            await self._sdk.start(sdk_sandbox)

    async def stop(self, sandbox: DaytonaRemoteSandbox) -> None:
        await self._sdk.stop(cast(SdkDaytonaRemoteSandbox, sandbox).sdk_sandbox)

    async def delete(self, sandbox: DaytonaRemoteSandbox) -> None:
        await self._sdk.delete(cast(SdkDaytonaRemoteSandbox, sandbox).sdk_sandbox)


def _extract_daytona_workspace_archive(
    content: bytes,
    root: Path,
    *,
    max_bytes: int,
    max_members: int,
) -> None:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
    except tarfile.TarError:
        raise ValueError("invalid Daytona workspace archive") from None
    total = 0
    with archive:
        members = archive.getmembers()
        if len(members) > max_members:
            raise ValueError("Daytona workspace exceeds collection member limit")
        for member in members:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe Daytona workspace archive member")
            parts = tuple(part for part in relative.parts if part not in {"", "."})
            if not parts:
                continue
            target = root.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("unsafe Daytona workspace archive member")
            total += member.size
            if total > max_bytes:
                raise ValueError("Daytona workspace exceeds collection size limit")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("invalid Daytona workspace archive")
            data = source.read(max_bytes + 1)
            if len(data) != member.size:
                raise ValueError("invalid Daytona workspace archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise ValueError("unsafe Daytona workspace archive member")
            if target.exists():
                if not target.is_file():
                    raise ValueError("unsafe Daytona workspace archive member")
                # Inputs are intentionally staged read-only. Collection replaces
                # the local mirror with the remote copy, so make an existing
                # worker-owned file writable before truncating it.
                target.chmod(0o600)
            target.write_bytes(data)
            # A remote archive may report mode 000. Keep the local control-plane
            # mirror owner-readable so snapshotting cannot fail after a
            # successful model response.
            target.chmod((member.mode & 0o755) | 0o400)


SessionKey = tuple[str, str]


@dataclass
class _SandboxEntry:
    sandbox: DaytonaRemoteSandbox
    owned: bool
    session_key: SessionKey | None
    released_at: float | None = None
    recovery_until: float | None = None
    prepared_cli: tuple[str, str] | None = None


@dataclass
class _SandboxLease:
    entry: _SandboxEntry
    session_lock: asyncio.Lock | None
    reusable: bool


class DaytonaSandboxProvider:
    def __init__(
        self,
        *,
        client: DaytonaClient,
        local_root: Path | None = None,
        snapshot: str | None = None,
        remote_workspace_root: str = "/home/daytona/harness",
        cli_version: str = "2.1.206",
        cli_path: str = "/home/daytona/.local/bin/claude",
        codex_cli_version: str = "0.149.0",
        codex_cli_path: str = "/home/daytona/.local/bin/codex",
        codex_cli_sha256: str = (
            "1c08ba262820b78d49ea7a93f326b6b430b72e5fe46830e433edef12e5123244"
        ),
        delete_on_destroy: bool = False,
        auto_stop_interval_minutes: int = 15,
        auto_delete_interval_minutes: int = 60,
        session_reuse_enabled: bool = False,
        session_idle_timeout_seconds: float = 600,
        warm_pool_max_sessions: int = 3,
        max_collect_bytes: int = 512 * 1024 * 1024,
        max_collect_members: int = 10_000,
        recovery_retention_seconds: float = 3600,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            max_collect_bytes <= 0
            or max_collect_members <= 0
            or session_idle_timeout_seconds <= 0
            or warm_pool_max_sessions <= 0
            or recovery_retention_seconds <= 0
        ):
            raise ValueError("Daytona lifecycle and collection limits must be positive")
        self._client = client
        self._local_root = local_root
        self._snapshot = snapshot
        self._remote_workspace_root = remote_workspace_root.rstrip("/")
        self._cli_version = cli_version
        self._cli_path = cli_path
        self._codex_cli_version = codex_cli_version
        self._codex_cli_path = codex_cli_path
        self._codex_cli_sha256 = codex_cli_sha256
        self._delete_on_destroy = delete_on_destroy
        self._auto_stop_interval_minutes = auto_stop_interval_minutes
        self._auto_delete_interval_minutes = auto_delete_interval_minutes
        self._session_reuse_enabled = session_reuse_enabled
        self._session_idle_timeout_seconds = session_idle_timeout_seconds
        self._warm_pool_max_sessions = warm_pool_max_sessions
        self._max_collect_bytes = max_collect_bytes
        self._max_collect_members = max_collect_members
        self._recovery_retention_seconds = recovery_retention_seconds
        self._monotonic = monotonic
        self._sandboxes: dict[str, _SandboxEntry] = {}
        self._leases: dict[str, _SandboxLease] = {}
        self._session_sandboxes: dict[SessionKey, str] = {}
        self._session_locks: dict[SessionKey, asyncio.Lock] = {}

    @staticmethod
    def _session_name(session_key: SessionKey) -> str:
        tenant_id, session_id = session_key
        digest = hashlib.sha256(f"{tenant_id}\0{session_id}".encode()).hexdigest()[:24]
        return f"harness-session-{digest}"

    def _drop_entry(self, entry: _SandboxEntry) -> None:
        self._sandboxes.pop(entry.sandbox.id, None)
        if (
            entry.session_key is not None
            and self._session_sandboxes.get(entry.session_key) == entry.sandbox.id
        ):
            self._session_sandboxes.pop(entry.session_key, None)
        if entry.session_key is not None:
            lock = self._session_locks.get(entry.session_key)
            if lock is not None and not lock.locked():
                self._session_locks.pop(entry.session_key, None)

    async def _retire(self, entry: _SandboxEntry) -> None:
        try:
            await self._client.stop(entry.sandbox)
            if self._delete_on_destroy and entry.owned:
                await self._client.delete(entry.sandbox)
        except DaytonaNotFoundError:
            pass
        else:
            self._drop_entry(entry)
            return
        self._drop_entry(entry)

    async def _create_session_sandbox(self, run: Run, session_key: SessionKey) -> _SandboxEntry:
        name = self._session_name(session_key)
        try:
            sandbox = await self._client.get(name)
        except DaytonaNotFoundError:
            try:
                sandbox = await self._client.create(
                    snapshot=self._snapshot,
                    name=name,
                    labels={
                        "harness.scope": "session",
                        "harness.tenant": run.tenant_id,
                        "harness.session": run.session_id,
                        "harness.created_by_run": run.run_id,
                    },
                    auto_stop_interval=self._auto_stop_interval_minutes,
                    auto_delete_interval=(
                        self._auto_delete_interval_minutes if self._delete_on_destroy else None
                    ),
                )
            except DaytonaConflictError:
                # A second worker won the deterministic-name create race.
                sandbox = await self._client.get(name)
                await self._client.start(sandbox)
        else:
            await self._client.start(sandbox)
        entry = _SandboxEntry(
            sandbox=sandbox,
            owned=True,
            session_key=session_key,
        )
        self._sandboxes[sandbox.id] = entry
        self._session_sandboxes[session_key] = sandbox.id
        return entry

    async def _acquire_session_entry(self, run: Run, session_key: SessionKey) -> _SandboxEntry:
        sandbox_id = self._session_sandboxes.get(session_key)
        entry = self._sandboxes.get(sandbox_id) if sandbox_id is not None else None
        if entry is not None:
            if await entry.sandbox.healthcheck():
                entry.released_at = None
                return entry
            logger.warning(
                "discarding unhealthy warm Daytona sandbox",
                extra={"sandbox_id": entry.sandbox.id, "session_id": run.session_id},
            )
            await self._retire(entry)
        return await self._create_session_sandbox(run, session_key)

    async def provision(self, run: Run) -> SandboxHandle:
        existing = run.input.get("daytona_sandbox_id")
        session_key = (run.tenant_id, run.session_id)
        reusable = self._session_reuse_enabled and not (isinstance(existing, str) and existing)
        session_lock: asyncio.Lock | None = None
        if reusable:
            session_lock = self._session_locks.setdefault(session_key, asyncio.Lock())
            await session_lock.acquire()
        try:
            if isinstance(existing, str) and existing:
                sandbox = await self._client.get(existing)
                await self._client.start(sandbox)
                entry = _SandboxEntry(
                    sandbox=sandbox,
                    owned=False,
                    session_key=None,
                )
                self._sandboxes[sandbox.id] = entry
            elif reusable:
                entry = await self._acquire_session_entry(run, session_key)
                sandbox = entry.sandbox
            else:
                sandbox = await self._client.create(
                    snapshot=self._snapshot,
                    name=f"harness-{run.run_id}-{run.fencing_token}"[:64],
                    labels={
                        "harness.tenant": run.tenant_id,
                        "harness.session": run.session_id,
                        "harness.run": run.run_id,
                    },
                    auto_stop_interval=self._auto_stop_interval_minutes,
                    auto_delete_interval=(
                        self._auto_delete_interval_minutes if self._delete_on_destroy else None
                    ),
                )
                entry = _SandboxEntry(
                    sandbox=sandbox,
                    owned=True,
                    session_key=None,
                )
                self._sandboxes[sandbox.id] = entry
            self._leases[sandbox.id] = _SandboxLease(
                entry=entry,
                session_lock=session_lock,
                reusable=reusable,
            )
        except BaseException:
            if session_lock is not None and session_lock.locked():
                session_lock.release()
            raise
        path = Path(tempfile.mkdtemp(prefix=f"{run.run_id}-", dir=self._local_root))
        remote_workspace = f"{self._remote_workspace_root}/{run.run_id}"
        config_digest = hashlib.sha256(f"{run.tenant_id}\0{run.session_id}".encode()).hexdigest()[
            :24
        ]
        remote_config_dir = f"{self._remote_workspace_root}/.claude-config/{config_digest}"

        def transport_factory(raw_options: object) -> object:
            if isinstance(raw_options, CodexAppServerOptions):
                return DaytonaCodexAppServerProcess(
                    session=sandbox.remote_session(),
                    options=raw_options,
                    remote_workspace=remote_workspace,
                    cli_path=self._codex_cli_path,
                    cli_version=self._codex_cli_version,
                    cli_sha256=self._codex_cli_sha256,
                )
            options = cast(ClaudeAgentOptions, raw_options)
            options.env = {
                **options.env,
                "CLAUDE_CONFIG_DIR": remote_config_dir,
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
            provider="daytona",
            isolation_level=SandboxIsolation.CONTAINER,
            remote_workspace=remote_workspace,
            runtime_transport_factory=transport_factory,
        )

    async def prepare(self, handle: SandboxHandle) -> None:
        entry = self._sandboxes[handle.sandbox_id]
        sandbox = entry.sandbox
        assert handle.remote_workspace is not None
        cli_identity = (self._cli_version, self._cli_path)
        if entry.prepared_cli != cli_identity:
            await sandbox.ensure_claude_cli(
                version=self._cli_version,
                path=self._cli_path,
            )
            entry.prepared_cli = cli_identity
        await sandbox.create_folder(handle.remote_workspace)
        for path in sorted(handle.path.rglob("*")):
            relative = path.relative_to(handle.path).as_posix()
            remote = f"{handle.remote_workspace}/{relative}"
            if path.is_dir():
                await sandbox.create_folder(remote)
            elif path.is_file() and not path.is_symlink():
                await sandbox.upload(remote, path.read_bytes())

    async def collect(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes[handle.sandbox_id].sandbox
        assert handle.remote_workspace is not None
        remote_files = await sandbox.list_files(handle.remote_workspace)
        if len(remote_files) > self._max_collect_members:
            raise ValueError("Daytona workspace exceeds collection member limit")
        declared_size = sum(
            size for _, is_dir, size in remote_files if not is_dir and size is not None
        )
        if declared_size > self._max_collect_bytes:
            raise ValueError("Daytona workspace exceeds collection size limit")
        content = await sandbox.download_archive(
            handle.remote_workspace,
            max_bytes=(self._max_collect_bytes + self._max_collect_members * 1024 + 10_240),
        )
        _extract_daytona_workspace_archive(
            content,
            handle.path,
            max_bytes=self._max_collect_bytes,
            max_members=self._max_collect_members,
        )

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
        sandbox = self._sandboxes[handle.sandbox_id].sandbox
        if handle.remote_workspace is None:
            raise ValueError("Daytona command requires a remote workspace")
        session = sandbox.remote_session()
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
        except TimeoutError:
            raise
        finally:
            await session.terminate()
        return SandboxCommandResult(
            exit_code=exit_code,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        lease = self._leases.pop(handle.sandbox_id, None)
        should_evict = False
        try:
            if lease is None:
                return
            if lease.reusable:
                if handle.preserve_remote_workspace:
                    now = self._monotonic()
                    lease.entry.released_at = now
                    lease.entry.recovery_until = max(
                        lease.entry.recovery_until or now,
                        now + self._recovery_retention_seconds,
                    )
                    should_evict = True
                    return
                try:
                    assert handle.remote_workspace is not None
                    await lease.entry.sandbox.remove_tree(handle.remote_workspace)
                except Exception:  # noqa: BLE001 - retire contaminated warm state
                    logger.exception(
                        "failed to clean warm Daytona workspace; retiring sandbox",
                        extra={"sandbox_id": handle.sandbox_id},
                    )
                    self._drop_entry(lease.entry)
                    try:
                        await self._retire(lease.entry)
                    except Exception:  # noqa: BLE001 - remote auto-delete is fallback
                        logger.exception(
                            "failed to retire contaminated Daytona sandbox",
                            extra={"sandbox_id": handle.sandbox_id},
                        )
                else:
                    lease.entry.released_at = self._monotonic()
                    should_evict = True
            else:
                self._drop_entry(lease.entry)
                await self._client.stop(lease.entry.sandbox)
                if (
                    self._delete_on_destroy
                    and lease.entry.owned
                    and not handle.preserve_remote_workspace
                ):
                    await self._client.delete(lease.entry.sandbox)
        finally:
            shutil.rmtree(handle.path, ignore_errors=True)
            if lease is not None and lease.session_lock is not None:
                if lease.session_lock.locked():
                    lease.session_lock.release()
                session_key = lease.entry.session_key
                if (
                    session_key is not None
                    and session_key not in self._session_sandboxes
                    and not lease.session_lock.locked()
                ):
                    self._session_locks.pop(session_key, None)
        if should_evict:
            await self._evict_excess_warm()

    async def _reap_warm_entry(
        self, entry: _SandboxEntry, *, released_before: float | None = None
    ) -> bool:
        session_key = entry.session_key
        if session_key is None:
            return False
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        await lock.acquire()
        try:
            current = self._sandboxes.get(entry.sandbox.id)
            if current is not entry or entry.released_at is None:
                return False
            if entry.recovery_until is not None and entry.recovery_until > self._monotonic():
                return False
            if released_before is not None and entry.released_at > released_before:
                return False
            await self._retire(entry)
            return True
        finally:
            lock.release()
            if session_key not in self._session_sandboxes and not lock.locked():
                self._session_locks.pop(session_key, None)

    async def _evict_excess_warm(self) -> int:
        warm = sorted(
            (
                entry
                for entry in self._sandboxes.values()
                if entry.released_at is not None
                and (entry.recovery_until is None or entry.recovery_until <= self._monotonic())
            ),
            key=lambda entry: cast(float, entry.released_at),
        )
        excess = max(0, len(warm) - self._warm_pool_max_sessions)
        reaped = 0
        for entry in warm[:excess]:
            try:
                retired = await self._reap_warm_entry(entry)
            except Exception:  # noqa: BLE001 - retry on the next maintenance pass
                logger.exception(
                    "failed to evict excess warm Daytona sandbox",
                    extra={"sandbox_id": entry.sandbox.id},
                )
            else:
                if not retired:
                    continue
                reaped += 1
        return reaped

    async def reap_expired(self) -> int:
        cutoff = self._monotonic() - self._session_idle_timeout_seconds
        expired = [
            entry
            for entry in tuple(self._sandboxes.values())
            if entry.released_at is not None
            and entry.released_at <= cutoff
            and (entry.recovery_until is None or entry.recovery_until <= self._monotonic())
        ]
        reaped = 0
        for entry in expired:
            try:
                retired = await self._reap_warm_entry(entry, released_before=cutoff)
            except Exception:  # noqa: BLE001 - retry on the next maintenance pass
                logger.exception(
                    "failed to reap expired warm Daytona sandbox",
                    extra={"sandbox_id": entry.sandbox.id},
                )
            else:
                if not retired:
                    continue
                reaped += 1
        return reaped + await self._evict_excess_warm()
