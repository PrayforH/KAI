"""Seeds separate the node login credential from per-instance server keys."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from harness.computer.vm_seed import create_vm_seed

COMPUTER = "computer_" + "a" * 32
INCARNATION = "b" * 32


def test_unique_keys_no_client_private_key_in_guest_seed_or_repr() -> None:
    first = create_vm_seed(COMPUTER, INCARNATION)
    second = create_vm_seed("computer_" + "c" * 32, "d" * 32)
    assert first.control_public_key != second.control_public_key
    assert first.host_public_key != second.host_public_key
    assert first.control_private_key not in first.user_data
    assert first.viewer_private_key not in first.user_data
    assert first.viewer_public_key not in {first.host_public_key, first.control_public_key}
    assert first.viewer_public_key != second.viewer_public_key
    assert "PRIVATE KEY" not in repr(first)
    assert "cloud-config" not in repr(first)
    body = json.loads(first.user_data.removeprefix(b"#cloud-config\n"))
    for client_key in (first.control_private_key, first.viewer_private_key):
        assert json.dumps(client_key.decode())[1:-1] not in first.user_data.decode()
    host = serialization.load_ssh_private_key(body["ssh_keys"]["ed25519_private"].encode(), None)
    assert isinstance(host, Ed25519PrivateKey)
    control = serialization.load_ssh_private_key(first.control_private_key, None)
    assert isinstance(control, Ed25519PrivateKey)
    assert (
        host.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
        == first.host_public_key
    )
    assert (
        control.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
        == first.control_public_key
    )
    assert first.host_public_key != first.control_public_key


def test_guest_seed_grants_only_the_fixed_root_owned_forced_command() -> None:
    seed = create_vm_seed(COMPUTER, INCARNATION)
    body = json.loads(seed.user_data.removeprefix(b"#cloud-config\n"))
    assert body["users"] == []
    assert body["allow_public_ssh_keys"] is False
    assert body["ssh_pwauth"] is False
    assert body["disable_root"] is True
    key, viewer, identity = body["write_files"]
    assert key["owner"] == "root:root"
    assert key["content"] == (
        'restrict,command="sudo -n /usr/bin/python3 -I /opt/kai-computer/ssh_gate.py" '
        + seed.control_public_key
        + "\n"
    )
    assert json.loads(identity["content"]) == {
        "version": 1,
        "computer_id": COMPUTER,
        "incarnation": INCARNATION,
    }
    assert viewer["owner"] == "root:root"
    assert viewer["content"] == (
        'restrict,command="sudo -n --preserve-env=SSH_ORIGINAL_COMMAND /usr/bin/python3 -I '
        '/opt/kai-computer/display_gate.py" ' + seed.viewer_public_key + "\n"
    )
    assert body["runcmd"] == [["systemctl", "restart", "ssh.service"]]
    assert json.loads(seed.meta_data)["instance-id"] == f"{COMPUTER}-{INCARNATION}"


@pytest.mark.parametrize(
    "computer,incarnation",
    [
        ("../../private", INCARNATION),
        (COMPUTER, "bad\nkey"),
        (COMPUTER, ""),
    ],
)
def test_no_paths_or_shell_syntax_in_identity(computer: str, incarnation: str) -> None:
    with pytest.raises(ValueError):
        create_vm_seed(computer, incarnation)


def test_host_pin_alias_is_independent_of_reused_port() -> None:
    seed = create_vm_seed(COMPUTER, INCARNATION)
    alias = "kai-computer-" + "a" * 32 + "-" + INCARNATION
    assert seed.host_key_alias == alias
    assert seed.known_hosts() == (alias + " " + seed.host_public_key + "\n").encode()


def test_qemu_user_network_does_not_reuse_a_builders_dhcp_identity() -> None:
    seed = create_vm_seed(COMPUTER, INCARNATION)
    network = json.loads(seed.network_data)["ethernets"]["kai"]
    assert network["match"] == {"name": "e*"}
    assert not network["dhcp4"] and not network["dhcp6"]
    assert network["addresses"] == ["10.0.2.15/24"]
    assert network["routes"] == [{"to": "0.0.0.0/0", "via": "10.0.2.2"}]
