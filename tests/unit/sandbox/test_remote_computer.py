"""Remote enrollment and recovery must fail closed without powering off a PC."""

import asyncio
import json
import shlex
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from harness.computer.models import ComputerLeaseLostError, LeasePurpose
from harness.computer.remote import RemoteComputerLifecycle, RemoteComputerSpec
from harness.computer.ssh_transport import SshEndpoint
from harness.sandbox.base import SandboxCommandResult


def endpoint():
    return SshEndpoint(
        host="remote-node",
        user="kai-control",
        identity="/private/key",
        known_hosts="/private/known_hosts",
        host_key_alias="remote-node",
        proxy_socket="/transport/ssh.sock",
    )


def spec():
    return RemoteComputerSpec(
        volume_ref="remote-fixture",
        computer_id="computer_" + "a" * 32,
        machine_id="b" * 32,
        guest_base_digest="sha256:" + "c" * 64,
        control=endpoint(),
        viewer=endpoint(),
    )


@pytest.mark.parametrize(
    "path", ["/tmp/sock;evil", "/tmp/sock $(evil)", "../sock", "/tmp/sock\ncmd"]
)
def test_reverse_proxy_rejects_shell_metacharacters(path):
    with pytest.raises(ValidationError):
        SshEndpoint.model_validate({**endpoint().model_dump(), "proxy_socket": path})


def test_private_ssh_uses_host_pin_and_no_password_fallback():
    argv = endpoint().argv()
    assert "StrictHostKeyChecking=yes" in argv
    assert "BatchMode=yes" in argv
    assert argv[-1] == "kai-control@remote-node"
    proxy = next(a.removeprefix("ProxyCommand=") for a in argv if a.startswith("ProxyCommand="))
    assert shlex.split(proxy)[0] == sys.executable
    assert shlex.split(proxy)[-1] == "/transport/ssh.sock"


@pytest.mark.asyncio
async def test_reverse_proxy_works_without_python_on_desktop_path(tmp_path: Path):
    async def exchange(reader, writer):
        writer.write(b"reply:" + await reader.readexactly(4))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    # macOS pytest paths can exceed the Unix socket name limit.
    with TemporaryDirectory(prefix="kai-ssh-", dir="/tmp") as directory:
        socket_path = Path(directory) / "ssh.sock"
        server = await asyncio.start_unix_server(exchange, path=socket_path)
        configured = endpoint().model_copy(update={"proxy_socket": str(socket_path)})
        proxy = next(
            a.removeprefix("ProxyCommand=")
            for a in configured.argv()
            if a.startswith("ProxyCommand=")
        )
        async with server:
            process = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                proxy,
                env={"PATH": str(tmp_path / "empty-path")},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(b"ping"), timeout=5)
    assert process.returncode == 0, stderr.decode()
    assert stdout == b"reply:ping"


@pytest.mark.asyncio
async def test_remote_recovery_drains_only_kai_and_preserves_physical_instance():
    target = spec()
    current = SimpleNamespace(
        purpose=LeasePurpose.RECOVER,
        epoch=9,
        lease_id="lease-fixture",
        expires_at=SimpleNamespace(timestamp=lambda: 9999999999),
    )
    computer = SimpleNamespace(
        provider="remote-computer",
        volume_ref=target.volume_ref,
        computer_id=target.computer_id,
        image=target.guest_base_digest,
    )

    @asynccontextmanager
    async def holder(_):
        yield computer, current

    registry = SimpleNamespace(host_operation=holder)
    gate = AsyncMock()
    gate.call.side_effect = [
        SandboxCommandResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "machine_id": target.machine_id,
                    "computer_id": target.computer_id,
                    "image": target.guest_base_digest,
                }
            ),
        ),
        SandboxCommandResult(exit_code=0, stdout="active"),
        SandboxCommandResult(exit_code=0, stdout="drained"),
    ]
    with (
        patch("harness.computer.remote.GuestGate", return_value=gate),
        patch("harness.computer.remote.validate_ack") as ack,
    ):
        lifecycle = RemoteComputerLifecycle(registry, {target.volume_ref: target})
        assert await lifecycle.lifecycle(current, "reset") == "remote_" + target.machine_id
    assert [c.args[0] for c in gate.call.await_args_list] == ["identity", "activate", "deactivate"]
    assert ack.call_count == 2


@pytest.mark.asyncio
async def test_declared_identity_mismatch_never_executes_or_fences():
    target = spec()

    @asynccontextmanager
    async def holder(_):
        yield (
            SimpleNamespace(
                provider="remote-computer",
                volume_ref=target.volume_ref,
                computer_id="wrong",
                image=target.guest_base_digest,
            ),
            SimpleNamespace(),
        )

    with patch("harness.computer.remote.GuestGate") as gate:
        with pytest.raises(ComputerLeaseLostError):
            await RemoteComputerLifecycle(
                SimpleNamespace(host_operation=holder), {target.volume_ref: target}
            ).lifecycle(None, "ensure")
        gate.assert_not_called()
