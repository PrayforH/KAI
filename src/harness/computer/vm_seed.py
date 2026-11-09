"""Per-computer NoCloud identity, generated only inside the private node.

The sealed OS base contains no login keys. A seed has a unique SSH server key
and the forced control *public* key. Its client private key stays on the node.
This module provisions identity; it does not authorize user requests or leases.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CONTROL_COMMAND = "sudo -n /usr/bin/python3 -I /opt/kai-computer/ssh_gate.py"
_VIEW_COMMAND = (
    "sudo -n --preserve-env=SSH_ORIGINAL_COMMAND "
    "/usr/bin/python3 -I /opt/kai-computer/display_gate.py"
)


def _private(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )


def _public(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode("ascii")
    )


@dataclass(frozen=True)
class ComputerVmSeed:
    """Sensitive material: save atomically in a node-only directory, never log."""

    computer_id: str
    instance_id: str
    host_public_key: str
    control_public_key: str
    viewer_public_key: str
    control_private_key: bytes = field(repr=False)
    viewer_private_key: bytes = field(repr=False)
    user_data: bytes = field(repr=False)
    meta_data: bytes = field(repr=False)
    network_data: bytes = field(repr=False)

    @property
    def host_key_alias(self) -> str:
        # A logical HostKeyAlias decouples pinning from a reused local port.
        return "kai-computer-" + self.instance_id.removeprefix("computer_")

    def known_hosts(self) -> bytes:
        return f"{self.host_key_alias} {self.host_public_key}\n".encode("ascii")


def create_vm_seed(computer_id: str, incarnation: str) -> ComputerVmSeed:
    """A new OS disk gets a fresh incarnation; retries reuse its saved seed."""

    if (
        re.fullmatch(r"computer_[a-f0-9]{32}", computer_id) is None
        or re.fullmatch(r"[a-f0-9]{32}", incarnation) is None
    ):
        raise ValueError("invalid computer VM identity")
    host, control, viewer = (Ed25519PrivateKey.generate() for _ in range(3))
    host_public, control_public = _public(host), _public(control)
    instance_id = f"{computer_id}-{incarnation}"
    identity = {"version": 1, "computer_id": computer_id, "incarnation": incarnation}
    authorized = f'restrict,command="{_CONTROL_COMMAND}" {control_public}\n'
    viewer_public = _public(viewer)
    config: dict[str, object] = {
        "hostname": "kai-" + computer_id.removeprefix("computer_")[:20],
        "manage_etc_hosts": True,
        "users": [],
        "ssh_pwauth": False,
        "disable_root": True,
        "allow_public_ssh_keys": False,
        "ssh_deletekeys": True,
        "ssh_quiet_keygen": True,
        "ssh_publish_hostkeys": {"enabled": False},
        "ssh_keys": {
            "ed25519_private": _private(host).decode("ascii"),
            "ed25519_public": host_public,
        },
        "write_files": [
            {
                "path": "/var/empty/kai-control/.ssh/authorized_keys",
                "owner": "root:root",
                "permissions": "0644",
                "content": authorized,
            },
            {
                "path": "/var/empty/kai-view/.ssh/authorized_keys",
                "owner": "root:root",
                "permissions": "0644",
                "content": f'restrict,command="{_VIEW_COMMAND}" {viewer_public}\n',
            },
            {
                "path": "/etc/kai-computer/identity.json",
                "owner": "root:root",
                "permissions": "0644",
                "content": json.dumps(identity) + "\n",
            },
        ],
        # No arbitrary root shell commands or users from channel payloads.
        "runcmd": [["systemctl", "restart", "ssh.service"]],
    }
    return ComputerVmSeed(
        computer_id=computer_id,
        instance_id=instance_id,
        host_public_key=host_public,
        control_public_key=control_public,
        viewer_public_key=viewer_public,
        control_private_key=_private(control),
        viewer_private_key=_private(viewer),
        user_data=("#cloud-config\n" + json.dumps(config) + "\n").encode("ascii"),
        meta_data=(json.dumps({"instance-id": instance_id}) + "\n").encode("ascii"),
        network_data=Path(__file__).with_name("vm-network.json").read_bytes(),
    )
