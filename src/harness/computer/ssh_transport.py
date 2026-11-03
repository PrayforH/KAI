"""Pinned SSH endpoints shared by VM and user-provided computers.

Endpoints are node configuration, never model or browser arguments.
"""

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class SshEndpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9._:-]+$")
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(pattern=r"^[a-z_][a-z0-9_-]*$")
    identity: str = Field(pattern=r"^/")
    known_hosts: str = Field(pattern=r"^/")
    host_key_alias: str = Field(pattern=r"^[a-zA-Z0-9._-]+$")

    proxy_socket: str | None = Field(default=None, pattern=r"^/[a-zA-Z0-9_./-]+$")

    def argv(self) -> list[str]:
        argv = [
            "ssh",
            "-T",
            "-F",
            "/dev/null",
            "-p",
            str(self.port),
            "-i",
            self.identity,
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "UserKnownHostsFile=" + self.known_hosts,
            "-o",
            "HostKeyAlias=" + self.host_key_alias,
            "-o",
            "CheckHostIP=no",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=4",
            "--",
            self.user + "@" + self.host,
        ]
        if self.proxy_socket is not None:
            proxy = str(Path(__file__).with_name("socket_proxy.py"))
            argv[1:1] = ["-o", "ProxyCommand=python3 -I " + proxy + " " + self.proxy_socket]
        return argv


class SshComputerSpec(Protocol):
    guest_base_digest: str

    def ssh_endpoint(self, kind: Literal["control", "viewer"]) -> SshEndpoint: ...
