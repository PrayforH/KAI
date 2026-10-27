"""Docker lifecycle for one stable, owner-labelled Computer name and work disk."""

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import httpx

from harness.computer.access import ComputerAuthorizer
from harness.computer.models import (
    AssistantComputer,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    LeasePurpose,
)
from harness.computer.registry import ComputerRegistry


@dataclass(frozen=True)
class ComputerLimits:
    image: str
    memory_bytes: int = 4 * 1024**3
    nano_cpus: int = 2_000_000_000
    pids: int = 256
    network: str = "bridge"

    def __post_init__(self) -> None:
        if (
            not self.image
            or min(self.memory_bytes, self.nano_cpus, self.pids) <= 0
            or re.fullmatch(r"[A-Za-z0-9_.-]+", self.network) is None
            or self.network == "host"
        ):
            raise ValueError("computer host resource configuration is invalid")


def owner_labels(computer: AssistantComputer) -> dict[str, str]:
    return {
        "kai.computer.id": computer.computer_id,
        "kai.computer.tenant": computer.tenant_id,
        "kai.computer.user": computer.user_id,
    }


def verify_instance(info: dict[str, Any], computer: AssistantComputer) -> str:
    config, labels = info.get("Config", {}), owner_labels(computer)
    actual: dict[str, Any] = config.get("Labels") or {}
    identifier = info.get("Id")
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r"[a-f0-9]{64}", identifier) is None
        or config.get("Image") != computer.image
        or config.get("User") != "0:0"
        or config.get("Entrypoint") != ["/usr/bin/tail"]
        or config.get("Cmd") != ["-f", "/dev/null"]
        or any(actual.get(key) != value for key, value in labels.items())
    ):
        raise RuntimeError("computer identity or process configuration does not match its registry")
    host = info.get("HostConfig", {})
    security: list[str] = host.get("SecurityOpt") or []
    if (
        host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or not any(value in security for value in ["no-new-privileges", "no-new-privileges:true"])
        or "ALL" not in (host.get("CapDrop") or [])
    ):
        raise RuntimeError("computer privilege configuration does not match")
    if not any(
        mount.get("Type") == "volume"
        and mount.get("Name") == computer.volume_ref
        and mount.get("Destination") == "/home/kai"
        and mount.get("RW") is True
        for mount in info.get("Mounts", [])
    ):
        raise RuntimeError("computer work disk does not match its registry")
    if not any(
        mount.get("Type") == "volume"
        and mount.get("Name") == computer.volume_ref + "-control"
        and mount.get("Destination") == "/var/lib/kai-computer"
        and mount.get("RW") is True
        for mount in info.get("Mounts", [])
    ):
        raise RuntimeError("computer private control disk does not match its registry")
    return identifier


class DockerComputerLifecycle:
    def __init__(
        self,
        registry: ComputerRegistry,
        docker: httpx.AsyncClient,
        limits: ComputerLimits,
        *,
        authorizer: ComputerAuthorizer | None = None,
    ) -> None:
        self.registry, self.docker, self.limits = registry, docker, limits
        self.authorizer = authorizer

    async def lifecycle(self, token: ComputerLease, operation: str) -> str | None:
        async with self.registry.host_operation(token) as (computer, current):
            if computer.provider != "docker-computer":
                raise ComputerLeaseLostError("computer is not managed by this host")
            if self.authorizer is not None:
                await self.authorizer.authorize(computer, current, operation)
            if operation == "ensure" and current.purpose is LeasePurpose.START:
                return await self._ensure(computer)
            if operation != "reset" or current.purpose is not LeasePurpose.RECOVER:
                raise ComputerLeaseLostError("computer lifecycle purpose does not match")
            # A stable name finds a creation with a lost reply. Every mutation
            # then uses its immutable ID, never a name that could be recycled.
            found = await self.docker.get(f"/containers/{computer.volume_ref}/json")
            if found.status_code == 404:
                return None
            found.raise_for_status()
            identifier = verify_instance(found.json(), computer)
            removed = await self.docker.delete(
                f"/containers/{identifier}", params={"force": "true"}
            )
            if removed.status_code != 404:
                removed.raise_for_status()
            async with asyncio.timeout(30):
                while True:
                    check = await self.docker.get(f"/containers/{identifier}/json")
                    if check.status_code == 404:
                        return None
                    check.raise_for_status()
                    await asyncio.sleep(0.1)

    async def _ensure(self, computer: AssistantComputer) -> str:
        if computer.image != self.limits.image:
            raise ComputerStartRejectedError("computer image is not enabled on this host")
        image = await self.docker.get(f"/images/{computer.image}/json")
        if image.status_code == 404:
            raise ComputerStartRejectedError("computer image is not installed on this host")
        image.raise_for_status()
        labels: dict[str, Any] = image.json().get("Config", {}).get("Labels") or {}
        if labels.get("org.shdata.kai.computer.guard") != "1":
            raise ComputerStartRejectedError("computer image has no supported execution gate")
        expected = owner_labels(computer)
        volume = await self.docker.get(f"/volumes/{computer.volume_ref}")
        if volume.status_code == 404:
            if computer.volume_ready:
                raise RuntimeError("saved computer disk is missing; refusing an empty replacement")
            volume = await self.docker.post(
                "/volumes/create", json={"Name": computer.volume_ref, "Labels": expected}
            )
        volume.raise_for_status()
        if volume.json().get("Labels") != expected:
            raise RuntimeError("computer disk ownership does not match")
        control_name = computer.volume_ref + "-control"
        control_labels = {**expected, "kai.computer.role": "control"}
        control = await self.docker.get(f"/volumes/{control_name}")
        if control.status_code == 404:
            control = await self.docker.post(
                "/volumes/create", json={"Name": control_name, "Labels": control_labels}
            )
        control.raise_for_status()
        if control.json().get("Labels") != control_labels:
            raise RuntimeError("computer private disk ownership does not match")
        found = await self.docker.get(f"/containers/{computer.volume_ref}/json")
        if found.status_code == 404:
            created = await self.docker.post(
                "/containers/create",
                params={"name": computer.volume_ref},
                json={
                    "Image": computer.image,
                    "User": "0:0",
                    "WorkingDir": "/",
                    "Entrypoint": ["/usr/bin/tail"],
                    "Cmd": ["-f", "/dev/null"],
                    "Labels": expected,
                    "HostConfig": {
                        "Init": True,
                        "Privileged": False,
                        "ReadonlyRootfs": True,
                        "CapDrop": ["ALL"],
                        "CapAdd": [
                            "SETUID",
                            "SETGID",
                            "CHOWN",
                            "KILL",
                            "DAC_OVERRIDE",
                            "FOWNER",
                            "SYS_ADMIN",
                            "SYS_CHROOT",
                        ],
                        "SecurityOpt": ["no-new-privileges"],
                        "PidsLimit": self.limits.pids,
                        "Memory": self.limits.memory_bytes,
                        "NanoCpus": self.limits.nano_cpus,
                        "NetworkMode": self.limits.network,
                        "Tmpfs": {
                            "/tmp": "rw,nosuid,nodev,mode=1777",
                        },
                        "Mounts": [
                            {
                                "Type": "volume",
                                "Source": computer.volume_ref,
                                "Target": "/home/kai",
                                "ReadOnly": False,
                            },
                            {
                                "Type": "volume",
                                "Source": control_name,
                                "Target": "/var/lib/kai-computer",
                                "ReadOnly": False,
                            },
                        ],
                    },
                },
            )
            if created.status_code != 409:
                created.raise_for_status()
            # A delayed earlier create may win the name. Inspect before adoption.
            found = await self.docker.get(f"/containers/{computer.volume_ref}/json")
        found.raise_for_status()
        info: dict[str, Any] = found.json()
        identifier = verify_instance(info, computer)
        if info.get("State", {}).get("Running") is not True:
            started = await self.docker.post(f"/containers/{identifier}/start")
            started.raise_for_status()
        return identifier
