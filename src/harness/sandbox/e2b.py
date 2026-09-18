"""E2B SandboxProvider and Claude CLI transport adapters."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shlex
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

import httpx
from claude_agent_sdk import ClaudeAgentOptions
from e2b import AsyncSandbox, CommandExitException, NotFoundException
from e2b.connection_config import ConnectionConfig
from e2b.envd.versions import ENVD_COMMANDS_STDIN, ENVD_ENVD_CLOSE

from harness.core.models import Run
from harness.runtime.codex_app_server import CodexAppServerOptions, DaytonaCodexAppServerProcess
from harness.runtime.daytona_transport import DaytonaClaudeTransport, RemoteClaudeSession
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxEgress,
    SandboxHandle,
    SandboxIsolation,
    SandboxResourceUsage,
)
from harness.sandbox.claude_cli import (
    banner_matches,
    install_command,
    version_pin,
    version_text,
)

logger = logging.getLogger(__name__)


def _version_tuple(value: object) -> tuple[int, ...]:
    parts = str(value).split(".")
    numbers: list[int] = []
    for part in parts:
        digits = "".join(char for char in part if char.isdigit())
        numbers.append(int(digits) if digits else 0)
    return tuple(numbers)


def _parse_version(value: str) -> tuple[int, ...] | None:
    numbers = _version_tuple(value)
    return numbers if any(numbers) else None


# The data-plane floors come from the pinned SDK's own capability gates, so this
# check tracks the SDK rather than a hand-copied number.
_ENVD_FLOORS: tuple[tuple[tuple[int, ...], str], ...] = (
    (_version_tuple(ENVD_COMMANDS_STDIN), "stdin-capable commands"),
    (_version_tuple(ENVD_ENVD_CLOSE), "closing stdin to end a command"),
)



# Every sandbox this platform provisions carries both markers. A reap pass reads
# them to tell platform instances apart from any other caller's on the same
# deployment, and a platform sandbox without them is never touched.
_MANAGED_TENANT_KEY = "harness.tenant"
_MANAGED_RUN_KEY = "harness.run"
# Bounds one reap pass; a deployment hosting thousands of foreign sandboxes must
# not turn a maintenance tick into an unbounded scan.
_LIST_SCAN_LIMIT = 1000
# Records what an idle sandbox was kept as, so the next Run of the session can
# adopt it and the reaper can tell a deliberate idle instance from an orphan.
# The reaper skips these: they are reclaimed by the platform TTL once the
# session stops renewing them.
_KEEP_MARKER_KEY = "harness.keep"
# Idle policies: what happens to a sandbox when its Run finishes.
IDLE_DESTROY = "destroy"
IDLE_KEEP_WARM = "keep_warm"
IDLE_PAUSE = "pause"
_IDLE_POLICIES = frozenset({IDLE_DESTROY, IDLE_KEEP_WARM, IDLE_PAUSE})
# The egress policy a sandbox was created with. Reuse requires an exact match,
# so a Run can never inherit a wider policy than it declared.
_EGRESS_KEY = "harness.egress"


async def _platform_logs(
    base_url: str,
    headers: Mapping[str, str],
    sandbox_id: str,
    limit: int,
    *,
    path: str,
) -> tuple[str, ...]:
    """Read the platform's own log tail for one sandbox.

    A sandbox's own log explains why it failed in ways the Run's output cannot:
    it covers the window before execd answers, and the platform's view of
    teardown. Absence is reported as emptiness — diagnostics must never turn a
    failed Run into a differently failed one.
    """

    if limit <= 0:
        return ()
    try:
        async with httpx.AsyncClient(
            base_url=base_url, headers=dict(headers), timeout=30
        ) as client:
            response = await client.get(path.format(sandbox_id=sandbox_id))
    except Exception:  # noqa: BLE001 - diagnostics are best effort
        return ()
    if response.status_code >= 400:
        return ()
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is not usable log lines
        return ()
    return _parse_log_payload(payload, limit)


def _parse_log_payload(payload: object, limit: int) -> tuple[str, ...]:
    """Normalise a platform log payload into bounded lines.

    CubeSandbox labels a line `line`; the v2 shape labels it `message`. Accepting
    both keeps one reader working across deployments instead of silently
    returning nothing on one of them.
    """

    entries = payload.get("logs") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return ()
    lines: list[str] = []
    for entry in entries[-limit:]:
        if not isinstance(entry, Mapping):
            continue
        text = entry.get("line") or entry.get("message") or ""
        if not text:
            continue
        stamp = str(entry.get("timestamp", ""))
        lines.append(f"{stamp} {text}".strip())
    return tuple(lines)


class E2BRemoteSandbox(Protocol):
    id: str

    async def ensure_claude_cli(self, *, version: str, path: str) -> None: ...

    async def create_folder(self, path: str) -> None: ...

    async def upload(self, remote_path: str, content: bytes) -> None: ...

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]: ...

    async def download(self, remote_path: str) -> bytes: ...

    async def kill(self) -> None: ...

    async def renew(self, timeout_seconds: int) -> None: ...

    async def remove_tree(self, path: str) -> None: ...

    async def ping(self) -> bool: ...

    async def snapshot(self, name: str | None = None) -> str: ...

    async def pause(self) -> None: ...

    def remote_session(self) -> RemoteClaudeSession: ...


class E2BClient(Protocol):
    async def create(
        self,
        *,
        template: str,
        timeout: int,
        allow_internet_access: bool,
        metadata: Mapping[str, str],
        network: Mapping[str, object] | None = None,
        volume_mounts: Mapping[str, str] | None = None,
    ) -> E2BRemoteSandbox: ...

    async def list_managed(self) -> list[tuple[str, Mapping[str, str]]]:
        """List the sandboxes this platform created, with their Run markers."""
        ...

    async def kill_sandbox(self, sandbox_id: str) -> None:
        """Delete one sandbox by id; already-deleted instances are not an error."""
        ...

    async def attach(self, sandbox_id: str) -> E2BRemoteSandbox:
        """Bind to a sandbox this platform created earlier."""
        ...

    async def template_state(self, reference: str) -> str | None:
        """Report a template's platform status, or None when it cannot be read."""
        ...

    async def snapshot(self, sandbox_id: str, name: str | None = None) -> str:
        """Capture a sandbox as a reusable template and return its reference."""
        ...

    async def pause_sandbox(self, sandbox_id: str) -> None:
        """Snapshot a sandbox's live state and release its host resources."""
        ...

    async def logs(self, sandbox_id: str, limit: int) -> tuple[str, ...]:
        """Read the platform's own log tail for a sandbox."""
        ...

    async def metrics(self, sandbox_id: str) -> SandboxResourceUsage | None:
        """Sample a sandbox's resource usage, or None when unsupported."""
        ...

    async def envd_version(self, sandbox_id: str) -> str | None:
        """Report the data-plane version this sandbox runs, or None."""
        ...


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
        pin = version_pin(version)
        try:
            check = await self._sandbox.commands.run(f"{shlex.quote(path)} --version")
        except Exception:  # noqa: BLE001 - a missing CLI is an expected cache miss
            check = None
        if check is not None and check.exit_code == 0 and banner_matches(
            version_text(check.stdout, ""), pin
        ):
            return
        try:
            installed = await self._sandbox.commands.run(
                install_command(pin), timeout=180
            )
        except Exception as error:
            raise RuntimeError("failed to install the pinned Claude CLI in E2B") from error
        if installed.exit_code != 0:
            raise RuntimeError("failed to install the pinned Claude CLI in E2B")
        try:
            verified = await self._sandbox.commands.run(f"{shlex.quote(path)} --version")
        except Exception as error:
            raise RuntimeError("E2B Claude CLI version verification failed") from error
        if verified.exit_code != 0 or not banner_matches(
            version_text(verified.stdout, ""), pin
        ):
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

    async def renew(self, timeout_seconds: int) -> None:
        await self._sandbox.set_timeout(timeout_seconds)

    async def remove_tree(self, path: str) -> None:
        await self._sandbox.commands.run(f"rm -rf -- {shlex.quote(path)}")

    async def ping(self) -> bool:
        return await self._sandbox.is_running()

    async def snapshot(self, name: str | None = None) -> str:
        info = await self._sandbox.create_snapshot(name=name)
        return str(info.snapshot_id)

    async def pause(self) -> None:
        await self._sandbox.pause()

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
        network: Mapping[str, object] | None = None,
        volume_mounts: Mapping[str, str] | None = None,
    ) -> E2BRemoteSandbox:
        sandbox = await AsyncSandbox.create(
            template=template,
            timeout=timeout,
            secure=True,
            allow_internet_access=allow_internet_access,
            metadata=dict(metadata),
            api_key=self._api_key,
            network=cast(Any, dict(network)) if network is not None else None,
            volume_mounts=(
                {path: volume for path, volume in volume_mounts.items()}
                if volume_mounts
                else None
            ),
        )
        return SdkE2BRemoteSandbox(sandbox)

    async def list_managed(self) -> list[tuple[str, Mapping[str, str]]]:
        """List the sandboxes this platform created, with their Run markers.

        The SDK's own parser requires every field of its model, while CubeSandbox
        omits ``endAt`` for a sandbox it is still starting (observed on 174 as
        ``KeyError: 'endAt'`` out of ``ListedSandbox.from_dict``). This listing
        feeds the capacity gauge, warm reuse and the reaper, so a page whose shape
        we cannot read ends that pass instead of taking all of governance down
        with it. Ending early can only under-report: nothing is ever deleted on
        the strength of this call alone, since reaping still requires the Run
        markers and a liveness predicate.
        """

        entries: list[tuple[str, Mapping[str, str]]] = []
        paginator = AsyncSandbox.list(api_key=self._api_key)
        while paginator.has_next and len(entries) < _LIST_SCAN_LIMIT:
            try:
                page = await paginator.next_items(api_key=self._api_key)
            except Exception:  # noqa: BLE001 - an unreadable page is not fatal
                logger.warning("sandbox listing page could not be parsed; skipping the rest")
                break
            for info in page:
                metadata = dict(info.metadata or {})
                if _MANAGED_TENANT_KEY in metadata and _MANAGED_RUN_KEY in metadata:
                    entries.append((info.sandbox_id, metadata))
        return entries

    async def kill_sandbox(self, sandbox_id: str) -> None:
        # The SDK reports a missing sandbox as a False return, so teardown stays
        # idempotent when the platform TTL or another reaper got there first.
        await AsyncSandbox.kill(sandbox_id, api_key=self._api_key)

    async def attach(self, sandbox_id: str) -> E2BRemoteSandbox:
        sandbox = await AsyncSandbox.connect(sandbox_id, api_key=self._api_key)
        return SdkE2BRemoteSandbox(sandbox)

    async def template_state(self, reference: str) -> str | None:
        # The public E2B API does not enumerate templates, so a reference cannot
        # be validated here; the caller treats None as "unverifiable".
        del reference
        return None

    async def snapshot(self, sandbox_id: str, name: str | None = None) -> str:
        info = await AsyncSandbox.create_snapshot(sandbox_id, name=name, api_key=self._api_key)
        return str(info.snapshot_id)

    async def pause_sandbox(self, sandbox_id: str) -> None:
        await AsyncSandbox.pause(sandbox_id, api_key=self._api_key)

    async def logs(self, sandbox_id: str, limit: int) -> tuple[str, ...]:
        # E2B cloud exposes the tail under v2 and authenticates with X-API-KEY.
        # Only the CubeSandbox route below is verified against a live platform.
        return await _platform_logs(
            ConnectionConfig(api_key=self._api_key).api_url,
            {"X-API-KEY": self._api_key},
            sandbox_id,
            limit,
            path="/v2/sandboxes/{sandbox_id}/logs",
        )

    async def metrics(self, sandbox_id: str) -> SandboxResourceUsage | None:
        try:
            samples = await AsyncSandbox.get_metrics(sandbox_id, api_key=self._api_key)
        except Exception:  # noqa: BLE001 - an unsupported metrics route is not an error
            return None
        if not samples:
            return None
        latest = samples[-1]
        return SandboxResourceUsage(
            cpu_used_pct=float(latest.cpu_used_pct),
            mem_used_bytes=int(latest.mem_used),
            mem_total_bytes=int(latest.mem_total),
            disk_used_bytes=int(latest.disk_used),
            disk_total_bytes=int(latest.disk_total),
            sampled_at=latest.timestamp,
        )

    async def envd_version(self, sandbox_id: str) -> str | None:
        try:
            info = await AsyncSandbox.get_info(sandbox_id, api_key=self._api_key)
        except Exception:  # noqa: BLE001 - an absent version is not a failure here
            return None
        version = getattr(info, "envd_version", None)
        return str(version) if version else None


RunLiveness = Callable[[str, str], Awaitable[bool]]


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
        cli_version: str = "",
        cli_path: str = "/home/user/.local/bin/claude",
        max_collect_bytes: int = 512 * 1024 * 1024,
        max_collect_members: int = 10_000,
        provider_name: str = "e2b",
        codex_cli_path: str | None = None,
        codex_cli_version: str = "",
        codex_cli_sha256: str = "",
        run_is_active: RunLiveness | None = None,
        idle_policy: str = IDLE_DESTROY,
        volume_mounts: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout_seconds <= 0 or max_collect_bytes <= 0 or max_collect_members <= 0:
            raise ValueError("E2B lifecycle and collection limits must be positive")
        if idle_policy not in _IDLE_POLICIES:
            raise ValueError(f"unknown idle policy: {idle_policy}")
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
        self._run_is_active = run_is_active
        self._sandboxes: dict[str, E2BRemoteSandbox] = {}
        self._clock = clock
        self._idle_policy = idle_policy
        self._volume_mounts = dict(volume_mounts or {})
        self._environment_checked = False
        self._observed_envd_version: str | None = None
        # Renew halfway through the TTL so a live Run never approaches expiry,
        # and at most that often so tool-heavy Runs do not renew per call.
        self._renewal_interval = max(60.0, timeout_seconds / 2)
        self._renewed_at: dict[str, float] = {}

    def remote_workspace_for(self, run: Run) -> str:
        return f"{self._remote_workspace_root}/{run.run_id}"

    @staticmethod
    def egress_fingerprint(egress: SandboxEgress | None) -> str | None:
        """Identify the egress policy a sandbox was created with."""

        if egress is None or not egress.is_restrictive():
            return None
        encoded = json.dumps(
            {"allow": sorted(egress.allow_hosts), "deny": egress.deny_internet},
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def _sandbox_metadata(self, run: Run, egress: SandboxEgress | None) -> dict[str, str]:
        metadata = {
            "harness.tenant": run.tenant_id,
            "harness.session": run.session_id,
            "harness.run": run.run_id,
        }
        if self._idle_policy != IDLE_DESTROY:
            metadata[_KEEP_MARKER_KEY] = self._idle_policy
        fingerprint = self.egress_fingerprint(egress)
        if fingerprint is not None:
            metadata[_EGRESS_KEY] = fingerprint
        return metadata

    async def _reuse_warm_sandbox(
        self, run: Run, egress: SandboxEgress | None
    ) -> E2BRemoteSandbox | None:
        """Return this session's warm sandbox when it can be reused as-is.

        Reuse is only sound because the Worker serializes Runs of one session
        across replicas (the Redis session gate), so no two Runs can claim the
        same warm sandbox at once. The tenant, session and egress policy must all
        match, and the sandbox must still answer: a warm sandbox is a cache, so a
        miss simply provisions a new one.
        """

        expected = self.egress_fingerprint(egress)
        for sandbox_id, metadata in await self._client.list_managed():
            if not metadata.get(_KEEP_MARKER_KEY):
                continue
            if metadata.get(_MANAGED_RUN_KEY) == run.run_id:
                continue
            if (
                metadata.get(_MANAGED_TENANT_KEY) != run.tenant_id
                or metadata.get("harness.session") != run.session_id
                or metadata.get(_EGRESS_KEY) != expected
            ):
                continue
            sandbox = await self._client.attach(sandbox_id)
            if await self._is_healthy(sandbox):
                logger.info("reusing warm sandbox sandbox_id=%s", sandbox_id)
                return sandbox
            logger.warning("discarding unhealthy warm sandbox sandbox_id=%s", sandbox_id)
            try:
                await self._client.kill_sandbox(sandbox_id)
            except Exception:  # noqa: BLE001 - a stale cache entry is not fatal
                logger.warning("failed to discard warm sandbox sandbox_id=%s", sandbox_id)
        return None

    @staticmethod
    async def _is_healthy(sandbox: E2BRemoteSandbox) -> bool:
        try:
            return await sandbox.ping()
        except Exception:  # noqa: BLE001 - unreachable counts as unhealthy
            return False

    @staticmethod
    def egress_network(egress: SandboxEgress | None) -> dict[str, object] | None:
        """Translate a Run's egress requirement into the platform's parameters.

        Both E2B and CubeSandbox order policy as allow, then deny, then their own
        default — and CubeSandbox's default is *allow*, so an allow list on its
        own would not close the sandbox. Sending the deny-all alongside it is
        what makes "only these hosts" true on both platforms.
        """

        if egress is None or not egress.is_restrictive():
            return None
        network: dict[str, object] = {"deny_out": ["0.0.0.0/0"]}
        if egress.allow_hosts:
            network["allow_out"] = list(egress.allow_hosts)
        return network

    async def provision(self, run: Run) -> SandboxHandle:
        return await self.provision_with_egress(run, None)

    async def provision_with_egress(
        self, run: Run, egress: SandboxEgress | None
    ) -> SandboxHandle:
        """Provision with the Run's declared egress requirement applied.

        Kept separate from the shared provider contract so that backends which
        cannot express a policy are refused by the caller instead of silently
        ignoring it.
        """

        network = self.egress_network(egress)
        sandbox = (
            await self._reuse_warm_sandbox(run, egress)
            if self._idle_policy != IDLE_DESTROY
            else None
        )
        if sandbox is None:
            sandbox = await self._client.create(
                template=self._template,
                timeout=self._timeout_seconds,
                allow_internet_access=self._allow_internet_access,
                metadata=self._sandbox_metadata(run, egress),
                network=network,
                volume_mounts=self._volume_mounts or None,
            )
        # Checked once per provider lifetime: a data plane too old to close
        # stdin cannot run tool execution at all, so the Run must fail loudly
        # rather than stall at the first command.
        await self.validate_environment(sandbox.id)
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
        sandbox = self._sandboxes[handle.sandbox_id]
        await self._keep_alive(sandbox)
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

    async def _keep_alive(self, sandbox: E2BRemoteSandbox) -> None:
        """Extend the platform TTL while the Run is still using the sandbox.

        A declared runtime timeout may exceed the sandbox TTL, and the platform
        would delete a working sandbox at expiry. Renewal failures are logged
        rather than raised: the command can still succeed, and failing the Run
        over a transient API error would be worse than the TTL that still applies.
        """

        now = self._clock()
        last = self._renewed_at.get(sandbox.id)
        if last is not None and now - last < self._renewal_interval:
            return
        try:
            await sandbox.renew(self._timeout_seconds)
        except Exception:  # noqa: BLE001 - the platform TTL remains the backstop
            logger.warning("sandbox TTL renewal failed sandbox_id=%s", sandbox.id)
            return
        self._renewed_at[sandbox.id] = now

    async def destroy(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes.pop(handle.sandbox_id, None)
        self._renewed_at.pop(handle.sandbox_id, None)
        try:
            if sandbox is not None:
                if self._idle_policy == IDLE_DESTROY:
                    await sandbox.kill()
                else:
                    await self._retire_idle(handle, sandbox)
        finally:
            shutil.rmtree(handle.path, ignore_errors=True)

    async def _retire_idle(self, handle: SandboxHandle, sandbox: E2BRemoteSandbox) -> None:
        """Keep the sandbox for this session and drop only this Run's workspace.

        A session's next Run starts from a clean remote directory: the previous
        Run's files were collected before destroy, and leaving them would let one
        Run's output leak into the next. Removing the workspace fails closed —
        discarding the sandbox is cheaper than reusing a contaminated one.

        The two kept policies differ only in what is retained: ``keep_warm``
        leaves the container running (processes and memory intact, costing host
        resources), while ``pause`` snapshots the live state and releases the
        host, so a later Run resumes the same processes at no idle cost.
        """

        if handle.remote_workspace is None:
            await sandbox.kill()
            return
        try:
            await sandbox.remove_tree(handle.remote_workspace)
        except Exception:  # noqa: BLE001 - retire rather than reuse contaminated state
            logger.warning(
                "failed to clean idle workspace; retiring sandbox sandbox_id=%s",
                handle.sandbox_id,
            )
            try:
                await sandbox.kill()
            except Exception:  # noqa: BLE001 - the platform TTL remains the backstop
                logger.warning("failed to retire sandbox sandbox_id=%s", handle.sandbox_id)
            return
        if self._idle_policy != IDLE_PAUSE:
            return
        try:
            await sandbox.pause()
        except Exception:  # noqa: BLE001 - a failed pause leaves a warm sandbox
            logger.warning("failed to pause sandbox sandbox_id=%s", handle.sandbox_id)

    def bind_run_liveness(self, predicate: RunLiveness) -> None:
        """Supply the Run fact the sandbox backend cannot observe by itself."""

        self._run_is_active = predicate

    async def active_count(self) -> int:
        """Report how many platform sandboxes exist right now."""

        return len(await self._client.list_managed())

    async def sandbox_logs(self, handle: SandboxHandle, limit: int = 40) -> tuple[str, ...]:
        """Read the platform's log tail for this Run's sandbox."""

        return await self._client.logs(handle.sandbox_id, limit)

    async def sandbox_metrics(self, handle: SandboxHandle) -> SandboxResourceUsage | None:
        """Sample this Run's sandbox resources, when the platform reports them."""

        return await self._client.metrics(handle.sandbox_id)

    async def validate_environment(self, sandbox_id: str) -> str | None:
        """Refuse a data plane older than the operations this provider performs.

        The floors come from the pinned SDK rather than from a guess, and the
        check runs once per provider lifetime: it is the sandbox's own daemon
        version, not the control plane's, that decides whether tool execution can
        even be expressed.
        """

        if self._environment_checked:
            return self._observed_envd_version
        version = await self._client.envd_version(sandbox_id)
        if version is None:
            return None
        observed = _parse_version(version)
        for floor, operation in _ENVD_FLOORS:
            if observed is not None and observed < floor:
                raise ValueError(
                    f"{self.provider_name} data plane {version} is older than "
                    f"{floor} required for {operation}"
                )
        self._observed_envd_version = version
        self._environment_checked = True
        return version

    async def validate_template(self) -> str | None:
        """Refuse a template that is missing or not READY.

        A template reference is accepted as an ID or an alias, and the platform
        also keeps failed builds in its catalogue, so a wrong or broken template
        would otherwise only surface as a confusing create failure during a Run.
        Returns the observed status, or None when the platform cannot be asked.
        """

        state = await self._client.template_state(self._template)
        if state is None:
            return None
        if state == "MISSING":
            raise ValueError(
                f"{self.provider_name} template {self._template!r} is not in the "
                "platform catalogue (neither a template ID nor an alias)"
            )
        if state != "READY":
            raise ValueError(
                f"{self.provider_name} template {self._template!r} is {state}, not READY"
            )
        return state

    async def create_snapshot(self, handle: SandboxHandle, name: str | None = None) -> str:
        """Capture this Run's sandbox as a reusable template.

        Used to build a golden environment once (dependencies installed, tools
        baked in) so later Runs start from a template instead of reinstalling.
        """

        sandbox = self._sandboxes.get(handle.sandbox_id)
        if sandbox is None:
            raise ValueError("snapshot requires a sandbox this provider provisioned")
        return await sandbox.snapshot(name)

    async def reap_expired(self) -> int:
        """Delete sandboxes whose Run has already finished or disappeared.

        The platform enforces its own TTL, so this is not about expiry: it
        reclaims the instance a Worker leaves behind when it dies before running
        teardown, which otherwise holds capacity until that TTL elapses. A Run
        that is still active is never touched, and with no liveness predicate
        bound nothing is deleted at all — reaping must be opted into.
        """

        if self._run_is_active is None:
            return 0
        removed = 0
        for sandbox_id, metadata in await self._client.list_managed():
            if metadata.get(_KEEP_MARKER_KEY):
                # A kept sandbox outlives the Run that created it on purpose: it
                # is the session's cache (warm or paused) and the platform TTL
                # reclaims it once the session stops renewing. Deleting it here
                # would defeat the reuse it was kept for.
                continue
            try:
                still_needed = await self._run_is_active(
                    str(metadata[_MANAGED_TENANT_KEY]), str(metadata[_MANAGED_RUN_KEY])
                )
                if still_needed:
                    continue
                await self._client.kill_sandbox(sandbox_id)
            except Exception:  # noqa: BLE001 - one bad instance must not stop the pass
                logger.warning("sandbox reap failed sandbox_id=%s", sandbox_id)
                continue
            self._sandboxes.pop(sandbox_id, None)
            removed += 1
        return removed
