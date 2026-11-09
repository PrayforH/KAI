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
import re
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
SUPERVISOR_ROOT = Path("/var/lib/kai-computer")
SLOT = re.compile(r"desktop_[a-f0-9]{24}")
ACTIONS = frozenset({"init", "activate", "renew", "deactivate", "exec"})
NATIVE_ACTIONS = frozenset({"native_desktop", "native_barrier", "desktop_export"})
DESKTOP_ACTION = "desktop_session"
CONTROL = Path("/opt/kai-computer/computer_control.py")
BOT_WORKDISK = Path("/opt/kai-computer/bot_workdisk.py")
DESKTOP_KEYS = frozenset(
    {"op", "desktopId", "unixUser", "display", "profile", "workspace", "vncSocket"}
)
DESKTOP_OPS = frozenset({"ensure", "start", "stop", "status"})
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
        or action not in ACTIONS | NATIVE_ACTIONS | {DESKTOP_ACTION}
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
    expected_disk = spec.pop("bot_workdisk", None)
    if expected_disk is not None and not isinstance(expected_disk, str):
        raise ValueError("Bot workdisk identity is invalid")
    if action in NATIVE_ACTIONS:
        # Native UI is not an arbitrary supervisor command. It uses the same
        # bounded adapter/fence as remote PCs, configured only by the operator.
        if "desktop_id" in spec:
            _load_module(BOT_WORKDISK).require(expected=expected_disk)
        else:
            require_workdisk()
        native_request(action, spec)
        return
    if action == DESKTOP_ACTION:
        # A Bot desktop slot is provisioned by a fixed root module; the request
        # carries only slot descriptors, never a path, unit or command.
        if spec.get("op") in ("ensure", "start"):
            _load_module(BOT_WORKDISK).require(expected=expected_disk)
        desktop_request(spec)
        return
    _trusted(SUPERVISOR.parent, directory=True)
    _trusted(SUPERVISOR, directory=False)
    # The SSH server itself is outside the task namespace. Enforce the same
    # monotonic NNP boundary supplied by the Docker backend for every command.
    _enforce_nnp()
    scope = spec.get("scope") or ""
    if not isinstance(scope, str) or (scope and SLOT.fullmatch(scope) is None):
        raise ValueError("guest scope is invalid")
    if action != "deactivate" and scope:
        _load_module(BOT_WORKDISK).require(expected=expected_disk)
    elif action != "deactivate":
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
    identity: list[str] = []
    if scope and action == "exec":
        account = _load_module(CONTROL).slot_account(scope)
        identity = [str(account.pw_uid), str(account.pw_gid), account.pw_dir]
    root = SUPERVISOR_ROOT if not scope else SUPERVISOR_ROOT / "scopes" / scope
    _private_directory(root)
    spool = root / "spool"
    _private_directory(spool)
    request = spool / (uuid4().hex + ".json")
    fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
        os.execve(
            "/usr/bin/python3",
            ["/usr/bin/python3", "-I", str(SUPERVISOR), action, str(request), str(root), *identity],
            ENVIRONMENT,
        )
    finally:
        request.unlink(missing_ok=True)


def _enforce_nnp() -> None:
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
        raise PermissionError("guest control could not enforce process privileges")


def desktop_request(request: dict[str, Any]) -> None:
    if set(request) != DESKTOP_KEYS or request["op"] not in DESKTOP_OPS:
        raise ValueError("desktop session request is invalid")
    values = [
        request[key]
        for key in ("desktopId", "unixUser", "display", "profile", "workspace", "vncSocket")
    ]
    if any(not isinstance(value, str) for value in values):
        raise ValueError("desktop session request is invalid")
    _trusted(CONTROL.parent, directory=True)
    _trusted(CONTROL, directory=False)
    _enforce_nnp()
    result = subprocess.run(
        ["/usr/bin/python3", "-I", str(CONTROL), request["op"], *values],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=ENVIRONMENT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("desktop session control refused")
    print(json.dumps({"op": request["op"], "state": result.stdout.strip()}))


def _load_module(path: Path):
    _trusted(path.parent, directory=True)
    _trusted(path, directory=False)
    spec = importlib.util.spec_from_file_location(f"kai_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("guest control module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _native_slot(desktop_id: str) -> dict[str, object]:
    control = _load_module(CONTROL)
    account = control.slot_account(desktop_id)
    settings = control.SESSIONS / f"{desktop_id}.env"
    _trusted(settings.parent, directory=True)
    _trusted(settings, directory=False)
    values = dict(line.split("=", 1) for line in settings.read_text().splitlines())
    if (
        values["DESKTOP_ID"] != desktop_id
        or values["UNIX_USER"] != account.pw_name
        or not control.DISPLAY.fullmatch(values["DISPLAY_NUMBER"])
        or values["WORKSPACE"] != f"{account.pw_dir}/workspace"
    ):
        raise PermissionError("native desktop does not match the prepared slot")
    runtime = control.RUNTIME / desktop_id
    return {
        "desktop_uid": account.pw_uid,
        "desktop_unit": f"desktop-session@{desktop_id}.service",
        "display": ":" + values["DISPLAY_NUMBER"],
        "xauthority": str(runtime / "Xauthority"),
        "runtime_dir": str(runtime),
        "dbus_address": f"unix:path={runtime}/bus",
        "workspace": values["WORKSPACE"],
    }


def native_request(action: str, request: dict[str, Any]) -> None:
    state_root = None
    slot: dict[str, object] = {}
    if "desktop_id" in request:
        if action not in {"native_desktop", "native_barrier"}:
            raise ValueError("desktop slot is only accepted for native input")
        desktop_id = request["desktop_id"]
        if not isinstance(desktop_id, str) or SLOT.fullmatch(desktop_id) is None:
            raise ValueError("guest scope is invalid")
        if action == "native_desktop":
            slot = _native_slot(desktop_id)
        state_root = SUPERVISOR_ROOT / "scopes" / desktop_id
        if action == "native_barrier":
            # An explicitly opened desktop can have no prior Run scope.
            _trusted(SUPERVISOR_ROOT, directory=True)
            state_root.parent.mkdir(mode=0o700, exist_ok=True)
            _trusted(state_root.parent, directory=True)
            _private_directory(state_root)
        else:
            _trusted(state_root.parent, directory=True)
        request = {key: item for key, item in request.items() if key != "desktop_id"}
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
    config.update(slot)
    spec = importlib.util.spec_from_file_location("kai_native_desktop", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("native desktop adapter is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = {"state_root": state_root} if state_root is not None else {}
    with module.locked(**root):
        if action == "native_barrier":
            if request.keys() != {"epoch"}:
                raise ValueError("native desktop barrier is invalid")
            module.fence(request["epoch"], blocked=True, **root)
            print('{"drained":true}')
        elif action == "desktop_export":
            export_path = Path("/opt/kai-computer/desktop_export.py")
            _trusted(export_path, directory=False)
            export_spec = importlib.util.spec_from_file_location("kai_desktop_export", export_path)
            if export_spec is None or export_spec.loader is None:
                raise RuntimeError("desktop export adapter is missing")
            exporter = importlib.util.module_from_spec(export_spec)
            export_spec.loader.exec_module(exporter)
            module.fence(request["desktop_epoch"], blocked=False)
            print(json.dumps(exporter.run(request, state_root=ROOT), ensure_ascii=False))
        else:
            result = (
                module.run(config, request, state_root=state_root)
                if state_root is not None else module.run(config, request)
            )
            print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        # Never echo command bodies, environment values, credentials or paths.
        reasons = {
            "desktop export scope or deadline is invalid": "export_deadline",
            "desktop export lost its execution lease": "export_lease",
            "desktop input generation was revoked": "desktop_fence",
        }
        reason = reasons.get(str(error), "")
        diagnostic = f"{type(error).__name__}:{getattr(error, 'errno', None)}:{reason}"
        print("Computer guest control refused the request. diagnostic=" + diagnostic,
              file=sys.stderr)
        raise SystemExit(125) from None
