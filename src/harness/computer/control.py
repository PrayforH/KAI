"""Authenticated privileged transport, outside the unprivileged user computer.

These computers expose no execd service. Only this control plane can ask the
Docker daemon to run the fixed supervisor as root; arbitrary model commands
are dropped to the dedicated task UID by that supervisor.
"""

import io
import json
import re
import struct
import tarfile
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast
from uuid import uuid4

import httpx

from harness.computer.access import ComputerAuthorizer
from harness.computer.host import verify_instance
from harness.computer.models import (
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    LeasePurpose,
)
from harness.computer.registry import ComputerRegistry
from harness.sandbox.base import SandboxCommandResult

ControlAction = Literal["init", "activate", "renew", "deactivate", "exec", "acp"]
MAX_BYTES = 40 * 1024 * 1024
SUPERVISOR = "/opt/kai-computer/supervisor.py"
SPOOL = "/var/lib/kai-computer/spool"


class ComputerControl(Protocol):
    async def call(
        self, token: ComputerLease, action: ControlAction, payload: Mapping[str, object]
    ) -> SandboxCommandResult: ...


class HttpComputerControl:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def call(
        self, token: ComputerLease, action: ControlAction, payload: Mapping[str, object]
    ) -> SandboxCommandResult:
        duration = payload.get("timeout_seconds", 30)
        if not isinstance(duration, (int, float)) or not 0 < duration <= 3600:
            raise ValueError("computer execution duration is invalid")
        response = await self.client.post(
            "/v1/computer/control",
            json={
                "token": token.model_dump(mode="json"),
                "action": action,
                "payload": dict(payload),
            },
            timeout=httpx.Timeout(60 + float(duration), connect=10),
        )
        if response.status_code in {403, 409}:
            raise ComputerLeaseLostError("computer control refused this holder")
        if response.status_code >= 400:
            raise RuntimeError(f"computer control unavailable: HTTP {response.status_code}")
        return SandboxCommandResult.model_validate(response.json())

    async def check_image(self, image: str, *, provider: str | None = None) -> None:
        response = await self.client.post(
            "/v1/computer/image/check",
            json={"image": image, "provider": provider},
            timeout=30,
        )
        if response.status_code == 422:
            raise ComputerStartRejectedError("computer image is not available on this host")
        if response.status_code >= 400:
            raise RuntimeError(
                f"computer image validation unavailable: HTTP {response.status_code}"
            )
        try:
            confirmed = response.json()
        except ValueError:
            raise RuntimeError("computer image validation acknowledgement is invalid") from None
        if (
            not isinstance(confirmed, dict)
            or cast(dict[str, object], confirmed).get("available") is not True
        ):
            raise RuntimeError("computer image validation acknowledgement is invalid")

    async def desktop(
        self,
        token: ComputerLease,
        operation: str,
        payload: Mapping[str, object],
        *,
        expected_epoch: int,
    ) -> dict[str, object]:
        response = await self.client.post(
            "/v1/computer/desktop",
            json={
                "token": token.model_dump(mode="json"),
                "operation": operation,
                "payload": dict(payload),
                "expected_epoch": expected_epoch,
            },
            timeout=httpx.Timeout(90, connect=10),
        )
        if response.status_code in {403, 409}:
            raise ComputerLeaseLostError("computer desktop refused this holder")
        if response.status_code >= 400:
            raise RuntimeError(f"computer desktop unavailable: HTTP {response.status_code}")
        value: object = response.json()
        if not isinstance(value, dict):
            raise RuntimeError("computer desktop acknowledgement is invalid")
        return cast(dict[str, object], value)

    async def lifecycle(
        self, token: ComputerLease, operation: str, *, image: str | None = None
    ) -> str | None:
        payload: dict[str, object] = {
            "token": token.model_dump(mode="json"),
            "operation": operation,
        }
        if image is not None:
            payload["image"] = image
        response = await self.client.post(
            "/v1/computer/lifecycle",
            json=payload,
            timeout=120,
        )
        if response.status_code == 422:
            raise ComputerStartRejectedError("computer image is not available on this host")
        if response.status_code in {403, 409}:
            raise ComputerLeaseLostError("computer lifecycle refused this holder")
        if response.status_code >= 400:
            raise RuntimeError(f"computer lifecycle unavailable: HTTP {response.status_code}")
        reference: object = response.json().get("provider_ref")
        if reference is not None and not isinstance(reference, str):
            raise RuntimeError("computer lifecycle returned an invalid instance identity")
        return reference


class ComputerLifecycleHost(Protocol):
    """The node-side lifecycle surface one provider serves."""

    async def check_image(self, image: str) -> None: ...

    async def lifecycle(
        self, token: ComputerLease, operation: str, *, image: str | None = None
    ) -> str | None: ...


class ComputerControlRouter:
    """Dispatch by the canonical computer's provider, never by caller input.

    Every control request re-resolves the current database holder first, so a
    stale cached allocation cannot pick the transport of a different machine.
    """

    def __init__(
        self,
        registry: ComputerRegistry,
        controls: Mapping[str, ComputerControl],
    ) -> None:
        self.registry, self.controls = registry, dict(controls)

    async def call(
        self, token: ComputerLease, action: ControlAction, payload: Mapping[str, object]
    ) -> SandboxCommandResult:
        computer, _ = await self.registry.holder(token)
        control = self.controls.get(computer.provider)
        if control is None:
            raise ComputerLeaseLostError("computer provider is not served by this node")
        return await control.call(token, action, payload)


class ComputerLifecycleRouter:
    def __init__(
        self,
        registry: ComputerRegistry,
        hosts: Mapping[str, ComputerLifecycleHost],
    ) -> None:
        self.registry, self.hosts = registry, dict(hosts)

    async def check_image(self, image: str, *, provider: str | None = None) -> None:
        host = self.hosts.get(provider or "docker-computer")
        if host is None:
            raise ComputerStartRejectedError("computer provider is not served by this node")
        await host.check_image(image)

    async def lifecycle(
        self, token: ComputerLease, operation: str, *, image: str | None = None
    ) -> str | None:
        computer, _ = await self.registry.holder(token)
        host = self.hosts.get(computer.provider)
        if host is None:
            raise ComputerLeaseLostError("computer provider is not served by this node")
        return await host.lifecycle(token, operation, image=image)


class DockerComputerControl:
    """Narrow root executor; its Docker socket never enters a user computer.

    The caller's computer reference, expiry, UID and root command are never
    accepted from the request. They are resolved from the current database
    holder and checked against the actual Docker container and mounted disk.
    """

    def __init__(
        self,
        registry: ComputerRegistry,
        docker: httpx.AsyncClient,
        *,
        authorizer: ComputerAuthorizer | None = None,
    ) -> None:
        self.registry, self.docker = registry, docker
        self.authorizer = authorizer

    async def call(
        self, token: ComputerLease, action: ControlAction, payload: Mapping[str, object]
    ) -> SandboxCommandResult:
        computer, current = await self.registry.holder(token)
        purposes = (
            {LeasePurpose.START, LeasePurpose.RECOVER}
            if action == "init"
            else {LeasePurpose.EXECUTE}
        )
        if current.purpose not in purposes or computer.provider != "docker-computer":
            raise ComputerLeaseLostError("computer control purpose does not match its lease")
        if self.authorizer is not None:
            await self.authorizer.authorize(computer, current, action)
        reference = computer.provider_ref
        if reference is None or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", reference) is None:
            raise ComputerLeaseLostError("computer has no valid instance reference")
        container = reference
        inspected = await self.docker.get(f"/containers/{container}/json")
        inspected.raise_for_status()
        info: dict[str, Any] = inspected.json()
        verify_instance(info, computer)

        if action == "init":
            prepared = await self._run_root(container, ["python3", "-I", SUPERVISOR, "prepare"], 15)
            if prepared.exit_code != 0:
                raise RuntimeError("computer private control directory is unavailable")

        # The database is authoritative even when a long create/connect had to
        # renew its lease. Never use an expiry supplied by a remote caller.
        spec = {
            **payload,
            "epoch": current.epoch,
            "lease_id": current.lease_id,
            "expires_at": current.expires_at.timestamp(),
        }
        body = json.dumps(spec).encode()
        if len(body) > MAX_BYTES:
            raise ValueError("computer control request exceeds size limit")
        name = uuid4().hex + ".json"
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            entry = tarfile.TarInfo(name)
            entry.mode, entry.uid, entry.gid, entry.size = 0o600, 0, 0, len(body)
            archive.addfile(entry, io.BytesIO(body))
        uploaded = await self.docker.put(
            f"/containers/{container}/archive",
            params={"path": SPOOL},
            content=output.getvalue(),
            headers={"Content-Type": "application/x-tar"},
        )
        uploaded.raise_for_status()
        duration = payload.get("timeout_seconds", 30)
        if not isinstance(duration, (int, float)) or not 0 < duration <= 3600:
            raise ValueError("computer execution duration is invalid")
        # Revalidate after file transfer / Docker I/O. Neither a queued request
        # nor an old Worker may reuse an earlier account/Run authorization.
        computer, current = await self.registry.holder(token)
        if self.authorizer is not None:
            await self.authorizer.authorize(computer, current, action)
        return await self._run_root(
            container, ["python3", "-I", SUPERVISOR, action, f"{SPOOL}/{name}"], float(duration)
        )

    async def _run_root(
        self, container: str, argv: list[str], duration: float
    ) -> SandboxCommandResult:
        created = await self.docker.post(
            f"/containers/{container}/exec",
            json={
                "User": "0:0",
                "AttachStdin": False,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": False,
                "WorkingDir": "/",
                "Cmd": argv,
            },
        )
        created.raise_for_status()
        identifier = created.json().get("Id")
        if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{64}", identifier) is None:
            raise RuntimeError("Docker did not return an execution identity")
        streams = {1: bytearray(), 2: bytearray()}
        pending = bytearray()
        total = 0
        async with self.docker.stream(
            "POST",
            f"/exec/{identifier}/start",
            json={"Detach": False, "Tty": False},
            timeout=httpx.Timeout(30 + float(duration), connect=10),
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                pending.extend(chunk)
                while len(pending) >= 8:
                    stream, reserved, size = struct.unpack(">B3sI", pending[:8])
                    if stream not in streams or reserved != b"\0\0\0" or size > MAX_BYTES:
                        raise RuntimeError("Docker execution output framing is invalid")
                    if len(pending) < 8 + size:
                        break
                    total += size
                    if total > MAX_BYTES:
                        raise RuntimeError("computer execution output exceeds size limit")
                    streams[stream].extend(pending[8 : 8 + size])
                    del pending[: 8 + size]
        if pending:
            raise RuntimeError("Docker execution output ended mid-frame")
        finished = await self.docker.get(f"/exec/{identifier}/json")
        finished.raise_for_status()
        state = finished.json()
        if state.get("Running") is not False or type(state.get("ExitCode")) is not int:
            raise RuntimeError("Docker has not confirmed execution exit")
        result = SandboxCommandResult(
            exit_code=state["ExitCode"],
            stdout=streams[1].decode(errors="replace"),
            stderr=streams[2].decode(errors="replace"),
        )
        if result.exit_code == 125 and result.stderr.strip() == (
            "Computer execution was refused; check its lease and control state."
        ):
            raise ComputerLeaseLostError("computer execution gate refused this lease")
        return result
