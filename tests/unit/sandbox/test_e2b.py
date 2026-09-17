from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from harness.core.models import Run, RunStatus
from harness.sandbox.base import SandboxIsolation
from harness.sandbox.e2b import E2BSandboxProvider


class FakeRemoteSession:
    def __init__(self) -> None:
        self.started: tuple[list[str], str, dict[str, str]] | None = None
        self.terminated = False
        self.stdout = [b"connected\n", None]
        self.stderr = [None]

    async def stage_config(self, remote_directory: str, files: dict[str, bytes]) -> None:
        del remote_directory, files

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        self.started = (argv, cwd, env)

    async def write(self, data: str) -> None:
        del data

    async def end_input(self) -> None:
        return

    async def read_stdout(self) -> bytes | None:
        return self.stdout.pop(0)

    async def read_stderr(self) -> bytes | None:
        return self.stderr.pop(0)

    async def wait(self) -> int:
        return 0

    async def terminate(self) -> None:
        self.terminated = True


class FakeSandbox:
    def __init__(self) -> None:
        self.id = "e2b-sandbox-a"
        self.ensured_cli: tuple[str, str] | None = None
        self.folders: list[str] = []
        self.uploads: dict[str, bytes] = {}
        self.remote_files: dict[str, bytes] = {}
        self.killed = False
        self.session = FakeRemoteSession()

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        self.ensured_cli = (version, path)

    async def create_folder(self, path: str) -> None:
        self.folders.append(path)

    async def upload(self, remote_path: str, content: bytes) -> None:
        self.uploads[remote_path] = content

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]:
        return [
            (path, False, len(content))
            for path, content in self.remote_files.items()
            if path.startswith(remote_path + "/")
        ]

    async def download(self, remote_path: str) -> bytes:
        return self.remote_files[remote_path]

    async def kill(self) -> None:
        self.killed = True

    def remote_session(self) -> FakeRemoteSession:
        return self.session


class FakeClient:
    def __init__(self) -> None:
        self.sandbox = FakeSandbox()
        self.created: dict[str, Any] | None = None

    async def create(self, **parameters: Any) -> FakeSandbox:
        self.created = parameters
        return self.sandbox


def run() -> Run:
    return Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.PROVISIONING,
        idempotency_key="e2b",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        updated_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_e2b_provider_stages_executes_collects_and_kills(tmp_path: Path) -> None:
    client = FakeClient()
    provider = E2BSandboxProvider(
        client=client,
        local_root=tmp_path,
        template="harness-template",
        timeout_seconds=900,
        allow_internet_access=True,
        remote_workspace_root="/home/user/harness",
    )
    handle = await provider.provision(run())
    (handle.path / "inputs").mkdir()
    (handle.path / "inputs" / "facts.txt").write_text("facts")

    await provider.prepare(handle)
    result = await provider.execute(
        handle,
        ("bash", "-lc", "echo connected"),
        environment={"SAFE_FLAG": "yes"},
    )
    client.sandbox.remote_files["/home/user/harness/run-a/outputs/report.md"] = b"report"
    await provider.collect(handle)
    options = ClaudeAgentOptions()
    assert handle.runtime_transport_factory is not None
    handle.runtime_transport_factory(options)
    collected_report = (handle.path / "outputs" / "report.md").read_bytes()
    await provider.destroy(handle)

    assert client.created == {
        "template": "harness-template",
        "timeout": 900,
        "allow_internet_access": True,
        "metadata": {
            "harness.tenant": "tenant-a",
            "harness.session": "session-a",
            "harness.run": "run-a",
        },
    }
    assert handle.provider == "e2b"
    assert handle.isolation_level is SandboxIsolation.CONTAINER
    # No version pin by default: provisioning follows the CLI bundled with
    # claude-agent-sdk, which is the version the SDK is locked against.
    assert client.sandbox.ensured_cli == (
        "",
        "/home/user/.local/bin/claude",
    )
    assert client.sandbox.uploads["/home/user/harness/run-a/inputs/facts.txt"] == b"facts"
    assert result.stdout == "connected\n"
    assert client.sandbox.session.started == (
        ["bash", "-lc", "echo connected"],
        "/home/user/harness/run-a",
        {"SAFE_FLAG": "yes"},
    )
    assert collected_report == b"report"
    assert options.env["CLAUDE_CONFIG_DIR"].startswith("/home/user/harness/.claude-config/")
    assert client.sandbox.killed is True
    assert not handle.path.exists()


@pytest.mark.asyncio
async def test_e2b_collection_limits_are_enforced(tmp_path: Path) -> None:
    client = FakeClient()
    provider = E2BSandboxProvider(
        client=client,
        local_root=tmp_path,
        max_collect_bytes=3,
    )
    handle = await provider.provision(run())
    client.sandbox.remote_files["/home/user/harness/run-a/large.bin"] = b"1234"

    with pytest.raises(ValueError, match="collection size"):
        await provider.collect(handle)

    await provider.destroy(handle)
