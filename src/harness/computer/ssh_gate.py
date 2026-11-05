"""Forced SSH entrypoint for one guest's private Computer control account.

The host controller must authorize the current owner/Run before using its
guest-specific SSH key. This gate is not a public API or a substitute for that
authorization. Model commands reach only the fixed unprivileged supervisor.
Run with /usr/bin/python3 -I; no application packages or HOME imports are used.
"""

import ctypes
import importlib.util
import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

MAX_BYTES = 40 * 1024**2
ROOT = Path("/var/lib/kai-computer")
SUPERVISOR = Path("/opt/kai-computer/supervisor.py")
ACTIONS = frozenset({"init", "activate", "renew", "deactivate", "exec"})
NATIVE_ACTIONS = frozenset({"native_desktop", "native_barrier"})
ENVIRONMENT = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}


def decode_request(body: bytes) -> tuple[str, dict[str, Any]]:
    if len(body) > MAX_BYTES:
        raise ValueError("guest request exceeds the allowed size")
    parsed: object = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("invalid guest request")
    value = cast(dict[str, Any], parsed)
    action, spec = value.get("action"), value.get("spec")
    if (
        set(value) != {"version", "action", "spec"}
        or type(value.get("version")) is not int
        or value["version"] != 1
        or not isinstance(action, str)
        or action not in ACTIONS | NATIVE_ACTIONS
        or not isinstance(spec, dict)
    ):
        raise ValueError("invalid guest request")
    return action, cast(dict[str, Any], spec)


def _trusted(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("guest control files are not trusted")


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _trusted(path, directory=True)
    if path.stat().st_mode & 0o077:
        raise PermissionError("guest control directory is not private")


def shared_home_is_mounted(mountinfo: str, mount_id: str) -> bool:
    """Check the mount actually opened, not a covered older mount at this path."""
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not fields or fields[0] != mount_id:
            continue
        return (
            bool(separator)
            and len(fields) >= 6
            and len(filesystem) >= 2
            and fields[3:5] == ["/", "/home/kai"]
            and {"rw", "nosuid", "nodev"} <= set(fields[5].split(","))
            and filesystem[:2] == ["9p", "kai_home"]
        )
    return False


def _require_workdisk() -> None:
    fd = os.open("/home/kai", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 28000 or info.st_gid != 28000:
            raise PermissionError("guest workdisk ownership does not match")
        identifiers = [
            line.split(":", 1)[1].strip()
            for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
            if line.startswith("mnt_id:")
        ]
        if len(identifiers) != 1 or not shared_home_is_mounted(
            Path("/proc/self/mountinfo").read_text(), identifiers[0]
        ):
            raise RuntimeError("guest workdisk is not mounted")
    finally:
        os.close(fd)


def run(*, require_workdisk: Callable[[], None] = _require_workdisk) -> None:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise PermissionError("guest control requires its privileged entrypoint")
    action, spec = decode_request(sys.stdin.buffer.read(MAX_BYTES + 1))
    if action in NATIVE_ACTIONS:
        # Native UI is not an arbitrary supervisor command. It uses the same
        # bounded adapter/fence as remote PCs, configured only by the operator.
        require_workdisk()
        native_request(action, spec)
        return
    _trusted(SUPERVISOR.parent, directory=True)
    _trusted(SUPERVISOR, directory=False)
    # The SSH server itself is outside the task namespace. Enforce the same
    # monotonic NNP boundary supplied by the Docker backend for every command.
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
        raise PermissionError("guest control could not enforce process privileges")
    if action != "deactivate":
        # A failed shared mount must never fall back to the guest OS's old or
        # empty HOME. Cleanup stays available even if the data disk is lost.
        require_workdisk()
    body = json.dumps(spec).encode()
    if len(body) > MAX_BYTES:
        raise ValueError("guest request exceeds the allowed size")
    if action == "init":
        subprocess.run(
            ["/usr/bin/python3", "-I", str(SUPERVISOR), "prepare"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=ENVIRONMENT,
            check=True,
        )
    _private_directory(ROOT)
    spool = ROOT / "spool"
    _private_directory(spool)
    request = spool / (uuid4().hex + ".json")
    fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
        os.execve(
            "/usr/bin/python3",
            ["/usr/bin/python3", "-I", str(SUPERVISOR), action, str(request)],
            ENVIRONMENT,
        )
    finally:
        request.unlink(missing_ok=True)


def native_request(action: str, request: dict[str, Any]) -> None:
    path = Path("/etc/kai-computer/native-desktop.json")
    module_path = Path("/opt/kai-computer/native_desktop.py")
    identity_path = Path("/etc/kai-computer/identity.json")
    for entry in (path, module_path, identity_path):
        _trusted(entry.parent, directory=True)
        _trusted(entry, directory=False)
    value = json.loads(path.read_bytes())
    if (value.keys() != {"enabled", "binary_sha256"} or value["enabled"] is not True
            or not isinstance(value["binary_sha256"], str)
            or len(value["binary_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in value["binary_sha256"])):
        raise PermissionError("native VM desktop is not configured")
    identity = json.loads(identity_path.read_bytes())
    config = {
        "computer_id": identity["computer_id"], "desktop_uid": 28001,
        "native_binary": "/opt/kai-agent-desktop/agent-desktop",
        "native_binary_sha256": value["binary_sha256"],
        "display": ":99", "xauthority": "/run/kai-desktop/Xauthority",
        "runtime_dir": "/run/kai-desktop",
        "dbus_address": "unix:path=/run/kai-desktop/bus",
        "private_desktop_session": True,
    }
    spec = importlib.util.spec_from_file_location("kai_native_desktop", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("native desktop adapter is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with module.locked():
        if action == "native_barrier":
            if request.keys() != {"epoch"}:
                raise ValueError("native desktop barrier is invalid")
            module.fence(request["epoch"], blocked=True)
            print('{"drained":true}')
        else:
            print(json.dumps(module.run(config, request), ensure_ascii=False))


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # Never echo command bodies, environment values, credentials or paths.
        print("Computer guest control refused the request.", file=sys.stderr)
        raise SystemExit(125) from None
