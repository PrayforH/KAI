# pyright: reportPrivateUsage=false

"""OpenSandbox provider, execd protocol and composition wiring."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from harness.composition import _runtime_sandbox, _sandbox
from harness.config import Settings
from harness.core.models import Run, RunStatus
from harness.sandbox.base import (
    SandboxCommandResult,
    SandboxEnforcement,
    SandboxIsolation,
    provider_meets_enforcement_floor,
    sandbox_enforcement,
)
from harness.sandbox.deferred import DeferredToolSandboxProvider
from harness.sandbox.opensandbox import (
    OpenSandboxClient,
    OpenSandboxCommandError,
    OpenSandboxSandboxProvider,
    _stream_exit_code,
    build_opensandbox_provider,
)


def run() -> Run:
    return Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.PROVISIONING,
        idempotency_key="opensandbox",
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
        updated_at=datetime(2026, 9, 17, tzinfo=UTC),
    )


class FakeRemote:
    def __init__(self, *, escape_path: str | None = None) -> None:
        self.id = "os-sandbox-a"
        self.folders: list[str] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.escape_path = escape_path
        self.remote_files: dict[str, bytes] = {
            "/workspace/run-a/report.txt": b"collected",
        }
        self.calls: list[dict[str, Any]] = []
        self.ensured_cli: tuple[str, str] | None = None
        self.killed = False

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        self.ensured_cli = (version, path)

    async def create_folder(self, path: str) -> None:
        self.folders.append(path)

    async def upload(self, remote_path: str, content: bytes) -> None:
        self.uploads.append((remote_path, content))

    async def upload_many(self, entries: Any) -> None:
        self.uploads.extend(entries)

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]:
        if self.escape_path is not None:
            return [(self.escape_path, False, 3)]
        entries: list[tuple[str, bool, int | None]] = [
            ("/workspace/run-a/nested", True, None)
        ]
        entries.extend(
            (path, False, len(content))
            for path, content in self.remote_files.items()
            if path.startswith(remote_path + "/")
        )
        return entries

    async def download(self, remote_path: str) -> bytes:
        return self.remote_files[remote_path]

    async def run(
        self,
        argv: Any,
        *,
        cwd: str,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        self.calls.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "environment": dict(environment or {}),
                "timeout_seconds": timeout_seconds,
            }
        )
        return SandboxCommandResult(exit_code=0, stdout="ok\n")

    async def kill(self) -> None:
        self.killed = True


class FakeClient:
    def __init__(self) -> None:
        self.remote = FakeRemote()
        self.created: dict[str, Any] | None = None

    async def create(self, **parameters: Any) -> FakeRemote:
        self.created = parameters
        return self.remote


def provider(client: FakeClient, tmp_path: Path) -> OpenSandboxSandboxProvider:
    return OpenSandboxSandboxProvider(
        client=client,  # pyright: ignore[reportArgumentType]
        image="python:3.12-slim",
        local_root=tmp_path,
        resource_limits={"cpu": "1000m", "memory": "2048Mi"},
    )


@pytest.mark.asyncio
async def test_provider_stages_executes_collects_and_kills(tmp_path: Path) -> None:
    client = FakeClient()
    subject = provider(client, tmp_path)
    (tmp_path / "seed").mkdir()
    handle = await subject.provision(run())
    handle.path.joinpath("inputs").mkdir()
    handle.path.joinpath("inputs", "prompt.txt").write_text("hello", encoding="utf-8")

    assert handle.provider == "opensandbox"
    assert handle.isolation_level is SandboxIsolation.CONTAINER
    assert handle.remote_workspace == "/workspace/run-a"
    assert client.created is not None
    assert client.created["metadata"]["harness.run"] == "run-a"
    assert client.created["entrypoint"] == ("tail", "-f", "/dev/null")

    await subject.prepare(handle)
    assert client.remote.folders == ["/workspace/run-a"]
    assert client.remote.uploads == [
        ("/workspace/run-a/inputs/prompt.txt", b"hello")
    ]

    result = await subject.execute(
        handle,
        ["bash", "-lc", "echo hi"],
        environment={"HARNESS": "1"},
        timeout_seconds=45,
    )
    assert result.exit_code == 0
    assert client.remote.calls == [
        {
            "argv": ["bash", "-lc", "echo hi"],
            "cwd": "/workspace/run-a",
            "environment": {"HARNESS": "1"},
            "timeout_seconds": 45,
        }
    ]

    await subject.collect(handle)
    assert handle.path.joinpath("nested").is_dir()
    assert handle.path.joinpath("report.txt").read_bytes() == b"collected"

    await subject.destroy(handle)
    assert client.remote.killed
    assert not handle.path.exists()


@pytest.mark.asyncio
async def test_collect_rejects_unsafe_or_oversized_results(tmp_path: Path) -> None:
    many = FakeClient()
    member_limit = OpenSandboxSandboxProvider(
        client=many,  # pyright: ignore[reportArgumentType]
        image="python:3.12-slim",
        local_root=tmp_path,
        max_collect_members=1,
    )
    member_handle = await member_limit.provision(run())
    with pytest.raises(ValueError, match="member limit"):
        await member_limit.collect(member_handle)

    size = FakeClient()
    size_limit = OpenSandboxSandboxProvider(
        client=size,  # pyright: ignore[reportArgumentType]
        image="python:3.12-slim",
        local_root=tmp_path,
        max_collect_bytes=4,
    )
    size_handle = await size_limit.provision(run())
    with pytest.raises(ValueError, match="size limit"):
        await size_limit.collect(size_handle)

    escaping_client = FakeClient()
    escaping_client.remote = FakeRemote(escape_path="/etc/passwd")
    escaping = OpenSandboxSandboxProvider(
        client=escaping_client,  # pyright: ignore[reportArgumentType]
        image="python:3.12-slim",
        local_root=tmp_path,
    )
    escaping_handle = await escaping.provision(run())
    with pytest.raises(ValueError, match="escaped local collection root"):
        await escaping.collect(escaping_handle)


@pytest.mark.asyncio
async def test_collect_overwrites_read_only_staged_input(tmp_path: Path) -> None:
    client = FakeClient()
    subject = provider(client, tmp_path)
    handle = await subject.provision(run())
    staged_input = handle.path / "inputs" / "original" / "工作簿1.xlsx"
    staged_input.parent.mkdir(parents=True)
    staged_input.write_bytes(b"staged")
    staged_input.chmod(0o444)
    client.remote.remote_files["/workspace/run-a/inputs/original/工作簿1.xlsx"] = b"remote"

    await subject.collect(handle)

    assert staged_input.read_bytes() == b"remote"
    assert staged_input.stat().st_mode & 0o400
    await subject.destroy(handle)


@pytest.mark.asyncio
async def test_execute_requires_remote_workspace_and_positive_timeout(tmp_path: Path) -> None:
    client = FakeClient()
    subject = provider(client, tmp_path)
    handle = await subject.provision(run())
    with pytest.raises(ValueError, match="timeout must be positive"):
        await subject.execute(handle, ["bash", "-lc", "true"], timeout_seconds=0)
    with pytest.raises(ValueError, match="remote workspace"):
        await subject.execute(
            handle.model_copy(update={"remote_workspace": None}),
            ["bash", "-lc", "true"],
        )


def test_stream_exit_code_reads_process_status_and_fails_closed() -> None:
    assert _stream_exit_code([{"type": "stdout", "text": "x"}]) == 0
    assert (
        _stream_exit_code(
            [
                {
                    "type": "error",
                    "error": {"ename": "CommandExecError", "evalue": "143"},
                }
            ]
        )
        == 143
    )
    with pytest.raises(OpenSandboxCommandError, match="no such file"):
        _stream_exit_code(
            [{"type": "error", "error": {"ename": "execd", "evalue": "no such file"}}]
        )


def test_opensandbox_declares_delegated_enforcement() -> None:
    assert sandbox_enforcement("opensandbox", SandboxIsolation.CONTAINER) is (
        SandboxEnforcement.DELEGATED
    )
    assert provider_meets_enforcement_floor("opensandbox", SandboxEnforcement.DELEGATED)
    assert not provider_meets_enforcement_floor("opensandbox", SandboxEnforcement.FULL)
    assert sandbox_enforcement(
        "opensandbox-deferred", SandboxIsolation.CONTAINER
    ) is SandboxEnforcement.DELEGATED


def test_configuration_is_required_before_a_backend_is_built() -> None:
    with pytest.raises(ValueError, match="HARNESS_OPENSANDBOX_API_URL"):
        build_opensandbox_provider(Settings(opensandbox_api_url=""))
    with pytest.raises(ValueError, match="HARNESS_OPENSANDBOX_API_KEY"):
        build_opensandbox_provider(
            Settings(opensandbox_api_url="http://172.20.109.115:8090")
        )


def test_deferred_mode_is_required_for_the_opensandbox_backend() -> None:
    settings = Settings(
        sandbox_provider="opensandbox",
        sandbox_execution_mode="remote_cli",
        opensandbox_api_url="http://172.20.109.115:8090",
        opensandbox_api_key=SecretStr("key"),
    )
    backend = _sandbox(settings)
    with pytest.raises(ValueError, match="worker_cli_deferred"):
        _runtime_sandbox(settings, backend)


def test_deferred_mode_wraps_the_opensandbox_backend() -> None:
    settings = Settings(
        sandbox_provider="opensandbox",
        sandbox_execution_mode="worker_cli_deferred",
        opensandbox_api_url="http://172.20.109.115:8090",
        opensandbox_api_key=SecretStr("key"),
    )
    backend = _sandbox(settings)
    assert isinstance(backend, OpenSandboxSandboxProvider)
    wrapped = _runtime_sandbox(settings, backend)
    assert isinstance(wrapped, DeferredToolSandboxProvider)
    assert wrapped is not backend


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_client_creates_waits_for_execd_and_reports_states() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            return httpx.Response(
                202, json={"id": "os-1", "status": {"state": "Pending"}}
            )
        if request.url.path == "/v1/sandboxes/os-1" and request.method == "GET":
            return httpx.Response(200, json={"id": "os-1", "status": {"state": "Running"}})
        if request.url.path.endswith("/proxy/44772/ping"):
            return httpx.Response(200, text="pong")
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    client = OpenSandboxClient(
        api_url="http://sandbox.example",
        api_key="key",
        transport=_transport(handler),
    )
    remote = await client.create(
        image="python:3.12-slim",
        timeout=600,
        entrypoint=["tail", "-f", "/dev/null"],
        resource_limits={"cpu": "1000m"},
        metadata={"harness.run": "run-a"},
    )
    assert remote.id == "os-1"
    assert "POST /v1/sandboxes" in seen
    await client.aclose()


@pytest.mark.asyncio
async def test_client_refuses_a_failed_sandbox() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "os-2", "status": {"state": "Pending"}})
        return httpx.Response(200, json={"id": "os-2", "status": {"state": "Failed"}})

    client = OpenSandboxClient(
        api_url="http://sandbox.example",
        api_key="key",
        transport=_transport(handler),
        ready_timeout_seconds=10,
    )
    with pytest.raises(RuntimeError, match="became Failed"):
        await client.create(
            image="python:3.12-slim",
            timeout=600,
            entrypoint=["tail", "-f", "/dev/null"],
            resource_limits={},
            metadata={},
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_client_discards_a_sandbox_that_never_became_ready() -> None:
    """A readiness failure must not leave an instance nothing tracks or reaps."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(202, json={"id": "os-3", "status": {"state": "Pending"}})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "os-3", "status": {"state": "Failed"}})

    client = OpenSandboxClient(
        api_url="http://sandbox.example",
        api_key="key",
        transport=_transport(handler),
        ready_timeout_seconds=10,
    )
    with pytest.raises(RuntimeError, match="became Failed"):
        await client.create(
            image="python:3.12-slim",
            timeout=600,
            entrypoint=["tail", "-f", "/dev/null"],
            resource_limits={},
            metadata={},
        )
    assert "DELETE /v1/sandboxes/os-3" in seen
    await client.aclose()


@pytest.mark.asyncio
async def test_client_discards_when_execd_cannot_be_reached() -> None:
    """An unreachable command plane is a provisioning failure, not a leak."""

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(202, json={"id": "os-4", "status": {"state": "Pending"}})
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path.endswith("/ping"):
            raise httpx.ConnectError("execd unreachable")
        return httpx.Response(200, json={"id": "os-4", "status": {"state": "Running"}})

    client = OpenSandboxClient(
        api_url="http://sandbox.example",
        api_key="key",
        transport=_transport(handler),
        ready_timeout_seconds=10,
    )
    with pytest.raises(httpx.ConnectError):
        await client.create(
            image="python:3.12-slim",
            timeout=600,
            entrypoint=["tail", "-f", "/dev/null"],
            resource_limits={},
            metadata={},
        )
    assert "DELETE /v1/sandboxes/os-4" in seen
    await client.aclose()


@pytest.mark.asyncio
async def test_remote_sandbox_speaks_the_exec_document_protocol() -> None:
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if request.url.path.endswith("/command"):
            body = b"".join(
                [
                    b'{"type":"init","text":"exec-1","timestamp":1}\n\n',
                    b'{"type":"stdout","text":"out","timestamp":2}\n\n',
                    b'{"type":"stderr","text":"err","timestamp":3}\n\n',
                    b'{"type":"error","timestamp":4,"error":{"ename":"CommandExecError",'
                    b'"evalue":"7","traceback":["exit status 7"]}}\n\n',
                ]
            )
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
        if request.url.path.endswith("/directories"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/directories/list"):
            return httpx.Response(
                200,
                json=[
                    {"path": "/w/a.txt", "type": "file", "size": 3},
                    {"path": "/w/sub", "type": "directory"},
                    {"path": "/w/link", "type": "symlink"},
                ],
            )
        if request.url.path.endswith("/files/download"):
            return httpx.Response(200, content=b"abc")
        if request.url.path.endswith("/files/upload"):
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    from harness.sandbox.opensandbox import EXECD_PORT, OpenSandboxRemoteSandbox

    client = httpx.AsyncClient(
        base_url="http://sandbox.example", transport=_transport(handler)
    )
    remote = OpenSandboxRemoteSandbox(
        sandbox_id="os-3",
        client=client,
        execd_base=f"/v1/sandboxes/os-3/proxy/{EXECD_PORT}",
    )
    result = await remote.run(
        ["bash", "-lc", "exit 7"], cwd="/w", environment={"A": "b"}, timeout_seconds=30
    )
    assert result.exit_code == 7
    assert result.stdout == "out"
    assert result.stderr == "err"
    command = next(r for r in recorded if r.url.path.endswith("/command"))
    assert command.headers["content-type"] == "application/json"
    assert b'"command":"bash -lc ' in command.content

    await remote.create_folder("/w")
    await remote.upload_many([("/w/a.txt", b"abc")])
    entries = await remote.list_files("/w")
    assert entries == [("/w/a.txt", False, 3), ("/w/sub", True, None), ("/w/link", False, None)]
    assert await remote.download("/w/a.txt") == b"abc"
    await client.aclose()


@pytest.mark.asyncio
async def test_upload_rejects_an_exec_failure(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, json={"code": "TOO_LARGE"})

    from harness.sandbox.opensandbox import EXECD_PORT, OpenSandboxRemoteSandbox

    client = httpx.AsyncClient(
        base_url="http://sandbox.example", transport=_transport(handler)
    )
    remote = OpenSandboxRemoteSandbox(
        sandbox_id="os-4",
        client=client,
        execd_base=f"/v1/sandboxes/os-4/proxy/{EXECD_PORT}",
    )
    with pytest.raises(RuntimeError, match="upload failed"):
        await remote.upload_many([("/w/a.txt", b"abc")])
    await client.aclose()


@pytest.mark.asyncio
async def test_upload_splits_batches_and_keeps_every_file(tmp_path: Path) -> None:
    payloads: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(request.content)
        return httpx.Response(200, json={})

    from harness.sandbox.opensandbox import EXECD_PORT, OpenSandboxRemoteSandbox

    client = httpx.AsyncClient(
        base_url="http://sandbox.example", transport=_transport(handler)
    )
    remote = OpenSandboxRemoteSandbox(
        sandbox_id="os-5",
        client=client,
        execd_base=f"/v1/sandboxes/os-5/proxy/{EXECD_PORT}",
    )
    entries = [(f"/w/f{index}.txt", b"x" * 1024) for index in range(40)]
    await remote.upload_many(entries)
    assert len(payloads) == 2
    joined = b"".join(payloads)
    for remote_path, _ in entries:
        assert remote_path.encode() in joined
    await client.aclose()


def _remote(handler: Any) -> Any:
    from harness.sandbox.opensandbox import EXECD_PORT, OpenSandboxRemoteSandbox

    client = httpx.AsyncClient(
        base_url="http://sandbox.example", transport=_transport(handler)
    )
    return OpenSandboxRemoteSandbox(
        sandbox_id="os-cli",
        client=client,
        execd_base=f"/v1/sandboxes/os-cli/proxy/{EXECD_PORT}",
    )


@pytest.mark.asyncio
async def test_unpinned_cli_falls_back_to_the_installer_without_a_version(
    tmp_path: Path,
) -> None:
    recorded: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    remote = _remote(handler)
    bundle = tmp_path / "claude"
    bundle.write_bytes(b"not-an-elf")

    async def fake_run(argv: Any, **_: Any) -> SandboxCommandResult:
        recorded.append(list(argv))
        if argv[0] == "bash":  # the installer itself
            return SandboxCommandResult(exit_code=0)
        if len(recorded) == 1:  # cache probe: no CLI yet
            return SandboxCommandResult(exit_code=127, stderr="not found")
        return SandboxCommandResult(exit_code=0, stdout="2.1.274 (Claude Code)\n")

    remote.run = fake_run  # type: ignore[method-assign]
    await remote._ensure_binary(bundle, "/root/.local/bin/claude", None)

    installer = recorded[1]
    assert installer[:2] == ["bash", "-c"]
    assert "install.sh | bash" in installer[2]
    assert "-s " not in installer[2]
    assert recorded[2][-1] == "--version"


@pytest.mark.asyncio
async def test_pinned_cli_rejects_a_different_banner(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    remote = _remote(handler)
    bundle = tmp_path / "claude"
    bundle.write_bytes(b"\x7fELF" + b"\x00" * 32)

    async def fake_run(argv: Any, **_: Any) -> SandboxCommandResult:
        return SandboxCommandResult(exit_code=0, stdout="2.1.206 (Claude Code)\n")

    remote.run = fake_run  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="version verification failed"):
        await remote._ensure_binary(bundle, "/root/.local/bin/claude", "2.1.259")


@pytest.mark.asyncio
async def test_matching_cli_in_the_sandbox_skips_provisioning(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    uploads: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        uploads.append(request)
        return httpx.Response(200, json={})

    remote = _remote(handler)
    bundle = tmp_path / "claude"
    bundle.write_bytes(b"\x7fELF" + b"\x00" * 32)

    async def fake_run(argv: Any, **_: Any) -> SandboxCommandResult:
        calls.append(list(argv))
        return SandboxCommandResult(exit_code=0, stdout="2.1.259 (Claude Code)\n")

    remote.run = fake_run  # type: ignore[method-assign]
    await remote._ensure_binary(bundle, "/root/.local/bin/claude", "2.1.259")
    assert len(calls) == 1
    assert uploads == []
