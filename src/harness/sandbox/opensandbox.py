"""OpenSandbox lifecycle and execd command/file provider.

OpenSandbox splits one sandbox across two planes:

* the **Lifecycle API** creates, expires and destroys sandboxes;
* **execd** inside the sandbox runs commands and serves the file API.

The Lifecycle API also fronts execd through
``/v1/sandboxes/{sandbox_id}/proxy/{port}``, so the Worker needs exactly one
reachable endpoint and never has to resolve a per-sandbox ingress port. execd
carries no credentials of its own; reachability of that proxy is the whole
authorization boundary, which is why the Lifecycle API key stays on the API
side and every execd call is made through the proxy.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
import tempfile
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from claude_agent_sdk import ClaudeAgentOptions

from harness.config import Settings
from harness.core.models import Run
from harness.runtime.codex_app_server import (
    CodexAppServerOptions,
    DaytonaCodexAppServerProcess,
)
from harness.runtime.daytona_transport import DaytonaClaudeTransport
from harness.sandbox.base import SandboxCommandResult, SandboxHandle, SandboxIsolation
from harness.sandbox.claude_cli import (
    banner_matches,
    bundled_cli_path,
    install_command,
    version_pin,
    version_text,
)
from harness.sandbox.opensandbox_session import OpenSandboxPtySession

EXECD_PORT = 44_772
_SUPPORTED_SANDBOX_STATES = frozenset({"Running", "Pending"})
_TERMINAL_SANDBOX_STATES = frozenset({"Terminated", "Failed"})
_UPLOAD_BATCH_FILES = 32
_UPLOAD_BATCH_BYTES = 8 * 1024 * 1024
_OCTAL_MODE = re.compile(r"[0-7]{1,4}")


class OpenSandboxCommandError(RuntimeError):
    """execd reported a failure that is not a plain process exit status."""


def _is_linux_elf(path: Path) -> bool:
    """Whether a worker-side binary can run inside a Linux sandbox."""

    try:
        with path.open("rb") as source:
            return source.read(4) == b"\x7fELF"
    except OSError:
        return False


def _validated_base_url(value: str, *, name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"OpenSandbox {name} must be an HTTP(S) endpoint")
    return value.rstrip("/")


def _iter_stream_events(payload_chunks: Iterable[bytes]) -> Iterable[dict[str, Any]]:
    """Yield decoded events from execd's streaming execution response.

    execd answers ``/command`` with ``text/event-stream`` whose body is a
    sequence of JSON objects separated by blank lines. Older builds prefix them
    with ``data:``, so accept both shapes and skip anything undecodable.
    """

    buffer = ""
    for chunk in payload_chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for raw_line in block.splitlines():
                line = raw_line.removeprefix("data:").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event


def _stream_text(events: Iterable[dict[str, Any]], stream: str) -> str:
    """Rebuild one output stream from execd's line-framed events.

    execd emits one event per output line with the line terminator removed —
    ``\\n`` and ``\\r`` both terminate a line — and reports a blank line as the
    exact text ``"\\n"``. A line cannot contain a newline, so restoring the
    separators is unambiguous; only the terminator of the last line is not
    recoverable, and it is left off rather than invented.
    """

    lines = [str(event.get("text", "")) for event in events if event.get("type") == stream]
    return "\n".join("" if line == "\n" else line for line in lines)


def _stream_exit_code(events: Sequence[dict[str, Any]]) -> int:
    """Map execd's terminal error event onto a process exit status.

    execd reports process termination as ``error`` with
    ``CommandExecError``/``signal: killed`` and the status in ``evalue``. A
    non-numeric ``evalue`` is an execd-side failure rather than a process exit,
    so it fails closed instead of being flattened into ``1``.
    """

    for event in events:
        if event.get("type") != "error":
            continue
        error = event.get("error")
        if not isinstance(error, Mapping):
            continue
        value = str(error.get("evalue", ""))
        try:
            return int(value)
        except ValueError as exception:
            detail = error.get("ename") or "execd error"
            raise OpenSandboxCommandError(
                f"OpenSandbox command failed: {detail}: {value}"
            ) from exception
    return 0


class OpenSandboxRemoteSandbox:
    """execd command and file operations for one provisioned sandbox."""

    def __init__(
        self,
        *,
        sandbox_id: str,
        client: httpx.AsyncClient,
        execd_base: str,
        transfer_timeout_seconds: int = 600,
    ) -> None:
        if transfer_timeout_seconds <= 0:
            raise ValueError("OpenSandbox transfer timeout must be positive")
        self.id = sandbox_id
        self._client = client
        self._execd_base = execd_base.rstrip("/")
        self._execd_port = EXECD_PORT
        self._transfer_timeout = httpx.Timeout(float(transfer_timeout_seconds))

    def _execd(self, path: str) -> str:
        return f"{self._execd_base}{path}"

    def remote_session(self) -> OpenSandboxPtySession:
        """Open the interactive channel a remote CLI is driven through."""

        return OpenSandboxPtySession(
            client=self._client, sandbox_id=self.id, execd_port=self._execd_port
        )

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        await self._ensure_binary(bundled_cli_path(), path, version_pin(version))

    async def ensure_codex_cli(self, *, version: str, path: str) -> None:
        source = shutil.which("codex")
        if source is None:
            raise RuntimeError(
                "OpenSandbox Codex requires the pinned CLI in the Linux Worker"
            )
        await self._ensure_binary(
            Path(source).resolve(), path, version_pin(version), allow_installer=False
        )

    async def _ensure_binary(
        self,
        bundled: Path,
        path: str,
        pin: str | None,
        *,
        allow_installer: bool = True,
    ) -> None:
        """Ensure the CLI exists in the sandbox at ``path``.

        A production Worker bundles a Linux ELF, and uploading it keeps the
        sandbox independent of egress policy. On a development host the bundled
        binary belongs to the host platform, so the official installer stands in
        when it is allowed to; the banner check decides either way.
        """

        try:
            check = await self.run([path, "--version"], cwd="/", timeout_seconds=30)
            if check.exit_code == 0 and banner_matches(
                version_text(check.stdout, check.stderr), pin
            ):
                return
        except Exception:  # noqa: BLE001 - a missing CLI is an expected cache miss
            pass
        if _is_linux_elf(bundled):
            await self._upload_binary(bundled, path, pin)
            return
        if not allow_installer:
            raise RuntimeError("OpenSandbox Codex requires a Linux Worker binary")
        await self._install_binary(pin, path)

    async def _upload_binary(self, bundled: Path, path: str, pin: str | None) -> None:
        if not _is_linux_elf(bundled):
            raise RuntimeError("OpenSandbox remote CLI requires a Linux Worker")
        await self.create_folder(str(PurePosixPath(path).parent))
        with bundled.open("rb") as source:
            await self.upload_many(((path, source.read()),), mode=755)
        await self._verify_binary(path, pin)

    async def _install_binary(self, pin: str | None, path: str) -> None:
        """Install the CLI with its own installer, which writes into $HOME.

        The installer places the binary at ``$HOME/.local/bin/claude``, so the
        configured path has to be that location and HOME has to be explicit:
        execd does not guarantee it in the command environment.
        """

        installed = await self.run(
            ["bash", "-c", install_command(pin)],
            cwd="/",
            environment={"HOME": "/root"},
            timeout_seconds=300,
        )
        if installed.exit_code != 0:
            raise RuntimeError(
                "failed to install the Claude CLI in OpenSandbox: "
                f"{version_text(installed.stdout, installed.stderr)[-300:]}"
            )
        await self._verify_binary(path, pin)

    async def _verify_binary(self, path: str, pin: str | None) -> None:
        verified = await self.run([path, "--version"], cwd="/", timeout_seconds=30)
        observed = version_text(verified.stdout, verified.stderr)
        if verified.exit_code != 0 or not banner_matches(observed, pin):
            raise RuntimeError(
                f"OpenSandbox CLI version verification failed at {path}: {observed[:200]}"
            )

    async def ping(self) -> bool:
        response = await self._client.get(self._execd("/ping"))
        return response.status_code == 200

    async def create_folder(self, path: str) -> None:
        response = await self._client.post(
            self._execd("/directories"), json={path: None}
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenSandbox failed to create {path}: HTTP {response.status_code}"
            )

    async def upload(self, remote_path: str, content: bytes) -> None:
        await self.upload_many(((remote_path, content),))

    async def upload_many(
        self, entries: Sequence[tuple[str, bytes]], *, mode: int | None = None
    ) -> None:
        """Upload files in bounded multipart batches.

        The server proxy requires ``Content-Length`` for multipart requests, so
        batches are capped rather than streamed. execd parses the permission
        with base 0, so ``mode`` carries the octal digits as a decimal number
        (755 means 0o755).
        """

        if mode is not None and not _OCTAL_MODE.fullmatch(str(mode)):
            raise ValueError("OpenSandbox upload mode must be octal digits, e.g. 755")

        batch: list[tuple[str, bytes]] = []
        batch_bytes = 0

        async def flush() -> None:
            nonlocal batch, batch_bytes
            if not batch:
                return
            metadata: dict[str, Any] = {"path": batch[0][0]}
            if mode is not None:
                metadata["mode"] = mode
            files = []
            for path, _ in batch:
                entry_metadata = dict(metadata, path=path)
                files.append(
                    (
                        "metadata",
                        (
                            "metadata",
                            json.dumps(entry_metadata),
                            "application/json",
                        ),
                    )
                )
            files += [
                ("file", (path, content, "application/octet-stream"))
                for path, content in batch
            ]
            response = await self._client.post(
                self._execd("/files/upload"), files=files, timeout=self._transfer_timeout
            )
            batch = []
            batch_bytes = 0
            if response.status_code >= 400:
                raise RuntimeError(
                    f"OpenSandbox upload failed: HTTP {response.status_code}"
                )

        for remote_path, content in entries:
            if len(content) > _UPLOAD_BATCH_BYTES or (
                batch and batch_bytes + len(content) > _UPLOAD_BATCH_BYTES
            ) or len(batch) >= _UPLOAD_BATCH_FILES:
                await flush()
            batch.append((remote_path, content))
            batch_bytes += len(content)
        await flush()

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]:
        response = await self._client.get(
            self._execd("/directories/list"), params={"path": remote_path, "depth": 100}
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenSandbox failed to list {remote_path}: HTTP {response.status_code}"
            )
        entries = response.json()
        if not isinstance(entries, list):
            raise RuntimeError("OpenSandbox returned an unexpected directory listing")
        return [
            (
                str(entry.get("path", "")),
                str(entry.get("type", "")) == "directory",
                int(entry["size"]) if isinstance(entry.get("size"), int) else None,
            )
            for entry in entries
            if isinstance(entry, Mapping)
        ]

    async def download(self, remote_path: str) -> bytes:
        response = await self._client.get(
            self._execd("/files/download"),
            params={"path": remote_path},
            timeout=self._transfer_timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenSandbox failed to download {remote_path}: HTTP {response.status_code}"
            )
        return response.content

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        """Run one command through execd and return its bounded result.

        execd's ``command`` field is shell text; ``argv`` is rejected by the
        deployed builds, so the argument vector is quoted as one command line.
        """

        if not argv:
            raise ValueError("sandbox command argv must not be empty")
        body: dict[str, Any] = {
            "command": shlex.join(argv),
            "cwd": cwd,
            "timeout": max(1, int(timeout_seconds * 1000)),
        }
        if environment:
            body["envs"] = dict(environment)
        events: list[dict[str, Any]] = []
        timeout = httpx.Timeout(
            connect=self._client.timeout.connect,
            read=max(timeout_seconds + 30, 60),
            write=self._client.timeout.write,
            pool=self._client.timeout.pool,
        )
        async with self._client.stream(
            "POST", self._execd("/command"), json=body, timeout=timeout
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise RuntimeError(
                    f"OpenSandbox command rejected: HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
            chunk: list[bytes] = []
            async for payload in response.aiter_bytes():
                chunk.append(payload)
            events = list(_iter_stream_events(chunk))
        return SandboxCommandResult(
            exit_code=_stream_exit_code(events),
            stdout=_stream_text(events, "stdout"),
            stderr=_stream_text(events, "stderr"),
        )

    async def kill(self) -> None:
        await self._client.delete(f"/v1/sandboxes/{self.id}")


class OpenSandboxClient:
    """Lifecycle API client that also vends per-sandbox execd handles."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        request_timeout_seconds: int = 30,
        ready_timeout_seconds: int = 90,
        transfer_timeout_seconds: int = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        token = api_key.strip()
        if not token or "\n" in token or "\r" in token:
            raise ValueError("HARNESS_OPENSANDBOX_API_KEY is required")
        if request_timeout_seconds <= 0 or ready_timeout_seconds <= 0:
            raise ValueError("OpenSandbox timeouts must be positive")
        if transfer_timeout_seconds <= 0:
            raise ValueError("OpenSandbox transfer timeout must be positive")
        self._api_url = _validated_base_url(api_url, name="API URL")
        self._ready_timeout_seconds = ready_timeout_seconds
        self._transfer_timeout_seconds = transfer_timeout_seconds
        self._token = token
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._transport = transport
        # Built on first use: assembling a provider is not a reason to open
        # connections, and an idle client would outlive its owning container.
        self._client: httpx.AsyncClient | None = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._api_url,
                headers={"OPEN-SANDBOX-API-KEY": self._token},
                timeout=httpx.Timeout(self._request_timeout_seconds),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def sandbox_state(self, sandbox_id: str) -> str:
        response = await self.http.get(f"/v1/sandboxes/{sandbox_id}")
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenSandbox failed to read {sandbox_id}: HTTP {response.status_code}"
            )
        status = response.json().get("status")
        return str(status.get("state", "")) if isinstance(status, Mapping) else ""

    async def create(
        self,
        *,
        image: str,
        timeout: int,
        entrypoint: Sequence[str],
        resource_limits: Mapping[str, str],
        metadata: Mapping[str, str],
        network_policy: Mapping[str, Any] | None = None,
    ) -> OpenSandboxRemoteSandbox:
        if not image.strip():
            raise ValueError("OpenSandbox image is required")
        if not entrypoint:
            raise ValueError("OpenSandbox entrypoint is required with an image")
        body: dict[str, Any] = {
            "image": {"uri": image},
            "timeout": timeout,
            "entrypoint": list(entrypoint),
            "metadata": dict(metadata),
        }
        if resource_limits:
            body["resourceLimits"] = dict(resource_limits)
        if network_policy is not None:
            body["networkPolicy"] = dict(network_policy)
        response = await self.http.post("/v1/sandboxes", json=body)
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenSandbox failed to create a sandbox: HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        created = response.json()
        sandbox_id = str(created.get("id", ""))
        if not sandbox_id:
            raise RuntimeError("OpenSandbox returned no sandbox identifier")
        remote = OpenSandboxRemoteSandbox(
            sandbox_id=sandbox_id,
            client=self.http,
            execd_base=f"/v1/sandboxes/{sandbox_id}/proxy/{EXECD_PORT}",
            transfer_timeout_seconds=self._transfer_timeout_seconds,
        )
        await self._wait_until_ready(remote)
        return remote

    async def _wait_until_ready(self, remote: OpenSandboxRemoteSandbox) -> None:
        """Wait for the sandbox to run and execd to answer.

        A sandbox whose container started can still have execd starting; the
        provider may only report success once a command plane exists.
        """

        deadline = asyncio.get_running_loop().time() + self._ready_timeout_seconds
        last_state = ""
        while asyncio.get_running_loop().time() < deadline:
            last_state = await self.sandbox_state(remote.id)
            if last_state in _TERMINAL_SANDBOX_STATES:
                raise RuntimeError(f"OpenSandbox sandbox became {last_state}")
            if last_state in _SUPPORTED_SANDBOX_STATES and await remote.ping():
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(
            f"OpenSandbox sandbox {remote.id} was not ready within "
            f"{self._ready_timeout_seconds}s (state={last_state or 'unknown'})"
        )


class OpenSandboxSandboxProvider:
    """Run each Harness Run in a dedicated OpenSandbox sandbox."""

    def __init__(
        self,
        *,
        client: OpenSandboxClient,
        image: str,
        timeout_seconds: int = 3600,
        entrypoint: Sequence[str] = ("tail", "-f", "/dev/null"),
        resource_limits: Mapping[str, str] | None = None,
        network_policy: Mapping[str, Any] | None = None,
        local_root: Path | None = None,
        remote_workspace_root: str = "/workspace",
        cli_version: str = "",
        cli_path: str = "/root/.local/bin/claude",
        codex_cli_path: str | None = None,
        codex_cli_version: str = "",
        codex_cli_sha256: str = "",
        max_collect_bytes: int = 512 * 1024 * 1024,
        max_collect_members: int = 10_000,
        provider_name: str = "opensandbox",
    ) -> None:
        if timeout_seconds <= 0 or max_collect_bytes <= 0 or max_collect_members <= 0:
            raise ValueError("OpenSandbox lifecycle and collection limits must be positive")
        if not image.strip():
            raise ValueError("OpenSandbox image is required")
        if not remote_workspace_root.startswith("/"):
            raise ValueError("OpenSandbox remote workspace root must be absolute")
        if not cli_path.startswith("/") or any(char.isspace() for char in cli_path):
            raise ValueError("OpenSandbox Claude CLI path must be absolute")
        self._client = client
        self.provider_name = provider_name
        self._image = image
        self._timeout_seconds = timeout_seconds
        self._entrypoint = tuple(entrypoint)
        self._resource_limits = dict(resource_limits or {})
        self._network_policy = dict(network_policy) if network_policy is not None else None
        self._local_root = local_root
        self._remote_workspace_root = remote_workspace_root.rstrip("/")
        self._cli_version = cli_version
        self._cli_path = cli_path
        self._codex_cli_path = codex_cli_path
        self._codex_cli_version = codex_cli_version
        self._codex_cli_sha256 = codex_cli_sha256
        self._max_collect_bytes = max_collect_bytes
        self._max_collect_members = max_collect_members
        self._sandboxes: dict[str, OpenSandboxRemoteSandbox] = {}

    async def provision(self, run: Run) -> SandboxHandle:
        sandbox = await self._client.create(
            image=self._image,
            timeout=self._timeout_seconds,
            entrypoint=self._entrypoint,
            resource_limits=self._resource_limits,
            metadata={
                "harness.tenant": run.tenant_id,
                "harness.session": run.session_id,
                "harness.run": run.run_id,
            },
            network_policy=self._network_policy,
        )
        self._sandboxes[sandbox.id] = sandbox
        path = Path(tempfile.mkdtemp(prefix=f"{run.run_id}-", dir=self._local_root))
        remote_workspace = f"{self._remote_workspace_root}/{run.run_id}"

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
            await sandbox.ensure_claude_cli(version=self._cli_version, path=self._cli_path)
        await sandbox.create_folder(handle.remote_workspace)
        entries: list[tuple[str, bytes]] = []
        for path in sorted(handle.path.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(handle.path).as_posix()
            entries.append((f"{handle.remote_workspace}/{relative}", path.read_bytes()))
        await sandbox.upload_many(entries)

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        if timeout_seconds <= 0:
            raise ValueError("sandbox command timeout must be positive")
        if handle.remote_workspace is None:
            raise ValueError("OpenSandbox command requires a remote workspace")
        return await self._sandboxes[handle.sandbox_id].run(
            argv,
            cwd=handle.remote_workspace,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )

    async def collect(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes[handle.sandbox_id]
        assert handle.remote_workspace is not None
        entries = await sandbox.list_files(handle.remote_workspace)
        if len(entries) > self._max_collect_members:
            raise ValueError("OpenSandbox workspace exceeds collection member limit")
        declared_size = sum(size for _, is_dir, size in entries if not is_dir and size is not None)
        if declared_size > self._max_collect_bytes:
            raise ValueError("OpenSandbox workspace exceeds collection size limit")
        collected_size = 0
        remote_root = PurePosixPath(handle.remote_workspace)
        for remote_path, is_dir, _size in entries:
            candidate = PurePosixPath(remote_path)
            if not candidate.is_relative_to(remote_root):
                raise ValueError("OpenSandbox workspace path escaped local collection root")
            relative = candidate.relative_to(remote_root)
            local = handle.path.joinpath(*relative.parts)
            if is_dir:
                local.mkdir(parents=True, exist_ok=True)
                continue
            content = await sandbox.download(remote_path)
            collected_size += len(content)
            if collected_size > self._max_collect_bytes:
                raise ValueError("OpenSandbox workspace exceeds collection size limit")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)

    async def destroy(self, handle: SandboxHandle) -> None:
        sandbox = self._sandboxes.pop(handle.sandbox_id, None)
        try:
            if sandbox is not None:
                await sandbox.kill()
        finally:
            shutil.rmtree(handle.path, ignore_errors=True)


def build_opensandbox_provider(settings: Settings) -> OpenSandboxSandboxProvider:
    """Assemble the OpenSandbox provider from deployment settings."""

    if not settings.opensandbox_api_url.strip():
        raise ValueError("HARNESS_OPENSANDBOX_API_URL is required")
    api_key = settings.opensandbox_api_key.get_secret_value()
    if not api_key.strip():
        raise ValueError("HARNESS_OPENSANDBOX_API_KEY is required")
    resource_limits = {
        "cpu": settings.opensandbox_cpu,
        "memory": settings.opensandbox_memory,
    }
    network_policy = (
        None
        if settings.opensandbox_allow_internet_access
        else {"defaultAction": "deny", "egress": []}
    )
    return OpenSandboxSandboxProvider(
        client=OpenSandboxClient(
            api_url=settings.opensandbox_api_url,
            api_key=api_key,
            request_timeout_seconds=settings.opensandbox_request_timeout_seconds,
            ready_timeout_seconds=settings.opensandbox_ready_timeout_seconds,
            transfer_timeout_seconds=settings.opensandbox_transfer_timeout_seconds,
        ),
        image=settings.opensandbox_image,
        timeout_seconds=settings.opensandbox_timeout_seconds,
        resource_limits=resource_limits,
        network_policy=network_policy,
        remote_workspace_root=settings.opensandbox_remote_workspace_root,
        cli_version=settings.opensandbox_claude_cli_version,
        cli_path=settings.opensandbox_claude_cli_path,
        codex_cli_path=settings.opensandbox_codex_cli_path,
        codex_cli_version=settings.daytona_codex_cli_version,
        codex_cli_sha256=settings.daytona_codex_cli_sha256,
        max_collect_bytes=settings.workspace_archive_max_bytes,
        max_collect_members=settings.workspace_archive_max_members,
    )
