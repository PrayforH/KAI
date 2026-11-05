"""The forced guest command accepts only the versioned supervisor envelope."""

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.computer import ssh_gate


@pytest.mark.parametrize("action", sorted(ssh_gate.ACTIONS | ssh_gate.NATIVE_ACTIONS))
def test_fixed_supervisor_actions_preserve_the_spec_as_data(action: str) -> None:
    spec = {"epoch": 2, "argv": ["printf", "private-fixture"], "environment": {"A": "B"}}
    assert ssh_gate.decode_request(
        json.dumps({"version": 1, "action": action, "spec": spec}).encode()
    ) == (action, spec)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b"{}",
        b'{"version":true,"action":"exec","spec":{}}',
        b'{"version":2,"action":"exec","spec":{}}',
        b'{"version":1,"action":"namespace","spec":{}}',
        b'{"version":1,"action":"prepare","spec":{}}',
        b'{"version":1,"action":"/bin/bash","spec":{}}',
        b'{"version":1,"action":[],"spec":{}}',
        b'{"version":1,"action":"exec","spec":[]}',
        b'{"version":1,"action":"exec","spec":{},"program":"other.py"}',
    ],
)
def test_ssh_cannot_choose_a_root_program_or_an_unversioned_request(body: bytes) -> None:
    with pytest.raises(ValueError):
        ssh_gate.decode_request(body)


def test_size_limit_is_checked_before_json_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_gate, "MAX_BYTES", 8)

    def unexpected(_body: object):
        pytest.fail("oversized private input must not be parsed")

    monkeypatch.setattr(ssh_gate.json, "loads", unexpected)
    with pytest.raises(ValueError, match="allowed size"):
        ssh_gate.decode_request(b"private-fixture")


HOME_MOUNT = "100 20 0:99 / /home/kai rw,nosuid,nodev,relatime - 9p kai_home rw"


def test_missing_workdisk_cannot_fall_back_to_os_home_or_a_covered_mount() -> None:
    assert ssh_gate.shared_home_is_mounted(HOME_MOUNT, "100")
    covered = HOME_MOUNT + "\n101 100 0:98 / /home/kai rw - tmpfs tmpfs rw"
    assert not ssh_gate.shared_home_is_mounted(covered, "101")
    assert not ssh_gate.shared_home_is_mounted(HOME_MOUNT, "missing")


@pytest.mark.parametrize(
    "old,new",
    [
        ("9p kai_home", "9p other_owner"),
        ("9p kai_home", "ext4 /dev/vda1"),
        ("rw,nosuid,nodev,relatime", "ro,nosuid,nodev"),
        ("rw,nosuid,nodev,relatime", "rw,nodev"),
        ("rw,nosuid,nodev,relatime", "rw,nosuid"),
        ("/ /home/kai", "/subdir /home/kai"),
    ],
)
def test_workdisk_requires_the_expected_export_and_mount_restrictions(old: str, new: str) -> None:
    assert not ssh_gate.shared_home_is_mounted(HOME_MOUNT.replace(old, new), "100")


@pytest.mark.parametrize("extra", [{}, {"native_binary": "/bin/sh"}, {"desktop_uid": 0}])
def test_vm_native_gate_has_fixed_identity_and_environment(monkeypatch, extra):
    config = {"enabled": True, "binary_sha256": "a" * 64, **extra}
    trusted = []
    monkeypatch.setattr(ssh_gate, "_trusted", lambda path, **kwargs: trusted.append(path))
    monkeypatch.setattr(Path, "read_bytes", lambda path: json.dumps(
        config if path.name == "native-desktop.json" else {"computer_id": "computer_fixture"}
    ).encode())
    calls = []

    class Loader:
        def exec_module(self, module):
            module.locked = nullcontext
            module.run = lambda config, request: calls.append((config, request)) or {"ok": True}

    spec = SimpleNamespace(loader=Loader())
    monkeypatch.setattr(ssh_gate.importlib.util, "spec_from_file_location", lambda *args: spec)
    monkeypatch.setattr(
        ssh_gate.importlib.util, "module_from_spec", lambda *args: SimpleNamespace()
    )
    request = {"scope": "fixture", "deadline": 1, "desktop_epoch": 2,
               "payload": {"action": "status"}}
    if extra:
        with pytest.raises(PermissionError):
            ssh_gate.native_request("native_desktop", request)
        assert calls == []
    else:
        ssh_gate.native_request("native_desktop", request)
        actual, forwarded = calls[0]
        assert actual["desktop_uid"] == 28001
        assert actual["native_binary"] == "/opt/kai-agent-desktop/agent-desktop"
        assert actual["dbus_address"] == "unix:path=/run/kai-desktop/bus"
        assert actual["runtime_dir"] == "/run/kai-desktop"
        assert actual["private_desktop_session"] is True
        assert forwarded == request
        assert Path("/etc/kai-computer/native-desktop.json") in trusted
