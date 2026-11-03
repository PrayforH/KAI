"""Fixed, headless agent-desktop entrypoint for an enrolled Linux desktop.

This file is also installed independently on the remote PC. Keep it stdlib-only:
the root-owned remote gate pins it, validates the request, then drops to the
enrolled desktop UID before invoking the pinned native binary. No shell,
arbitrary flags, launch, clipboard, headed input or user-selected paths.
"""

import ctypes
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

REF = re.compile(r"@s[a-z0-9]+:e[0-9]+")
ACTIONS = {"status", "list_apps", "list_windows", "snapshot", "click", "type", "select"}
MAX_OUTPUT = 48 * 1024
ROOT = Path("/var/lib/kai-computer")


@contextmanager
def locked():
    info = ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError("computer control directory is not private")
    fd = os.open(ROOT / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def advance_fence(current: dict[str, Any], epoch: int, *, blocked: bool) -> dict[str, Any]:
    if type(epoch) is not int or epoch < 0:
        raise ValueError("desktop epoch is invalid")
    previous = current.get("epoch", 0)
    if previous > epoch or previous == epoch and current.get("blocked") and not blocked:
        raise PermissionError("desktop input generation was revoked")
    return {"epoch": epoch, "blocked": blocked}


def fence(epoch: int, *, blocked: bool) -> None:
    path = ROOT / "desktop-epoch.json"
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            current = cast(dict[str, Any], json.load(stream))
    except FileNotFoundError:
        current = {}
    updated = advance_fence(current, epoch, blocked=blocked)
    fd = os.open(
        ROOT / "desktop-epoch.next", os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as stream:
        json.dump(updated, stream)
    os.replace(ROOT / "desktop-epoch.next", path)


def command(payload: dict[str, Any]) -> list[str]:
    """Validate a closed protocol twice: on the node and on the physical PC."""
    allowed = {
        "action",
        "app",
        "window_id",
        "ref",
        "text",
        "root",
        "surface",
        "interactive",
        "skeleton",
        "max_depth",
    }
    if (
        payload.keys() - allowed
        or not isinstance(payload.get("action"), str)
        or payload["action"] not in ACTIONS
    ):
        raise ValueError("native desktop action is invalid")
    action = payload["action"]
    argv = [action.replace("_", "-")]
    if action in {"click", "type", "select"}:
        if payload.keys() - {"action", "ref", "text"}:
            raise ValueError("ref actions accept only a ref and text")
        ref = payload.get("ref")
        if not isinstance(ref, str) or REF.fullmatch(ref) is None:
            raise ValueError("use a qualified ref from this run's latest snapshot")
        argv += ["--timeout-ms=2000", "--", ref]
        if action != "click":
            text = payload.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 2000 or "\0" in text:
                raise ValueError("native input text is invalid")
            argv.append(text)
        elif "text" in payload:
            raise ValueError("click does not accept text")
        return argv
    if action in {"status", "list_apps"}:
        if payload.keys() != {"action"}:
            raise ValueError("inventory action takes no extra arguments")
        return argv
    if action == "list_windows" and payload.keys() - {"action", "app"}:
        raise ValueError("window inventory accepts only an app filter")
    for key, flag in (("app", "app"), ("window_id", "window-id"), ("root", "root")):
        if key not in payload:
            continue
        value = payload[key]
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 160
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("native desktop selector is invalid")
        if key == "root" and REF.fullmatch(value) is None:
            raise ValueError("snapshot root must be a qualified ref")
        argv.append(f"--{flag}={value}")
    argv.append("--timeout-ms=2000")
    if action == "snapshot":
        surface = payload.get("surface", "window")
        if surface not in {"window", "sheet", "alert", "menu"}:
            raise ValueError("native desktop surface is not supported")
        depth = payload.get("max_depth", 8)
        if type(depth) is not int or not 1 <= depth <= 15:
            raise ValueError("native snapshot depth is invalid")
        argv += ["--compact", f"--surface={surface}", f"--max-depth={depth}"]
        for key in ("interactive", "skeleton"):
            if key in payload and type(payload[key]) is not bool:
                raise ValueError("native snapshot option must be boolean")
        if payload.get("interactive", True):
            argv.append("-i")
        if payload.get("skeleton", False):
            if surface != "window":
                raise ValueError("do not skeleton an overlay")
            argv.append("--skeleton")
    return argv


def uncertain(code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "disposition": {"delivery": "unknown", "retry": "unsafe"},
            "suggestion": (
                "Read a fresh, narrower snapshot before deciding what to do; do not repeat input."
            ),
        },
    }


def run(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if request.keys() != {"scope", "deadline", "payload", "desktop_epoch"}:
        raise ValueError("native desktop request shape is invalid")
    scope = request["scope"]
    if not isinstance(scope, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", scope) is None:
        raise ValueError("native desktop scope is invalid")
    deadline = request["deadline"]
    if not isinstance(deadline, (int, float)) or not time.time() <= deadline <= time.time() + 15:
        raise ValueError("native desktop request expired")
    args = command(request["payload"])
    state = json.loads((ROOT / "state.json").read_bytes())
    if (
        not state.get("active")
        or state.get("lease_id") != scope
        or state.get("expires_at", 0) <= time.time()
    ):
        raise PermissionError("native action lost its execution lease")
    fence(request["desktop_epoch"], blocked=False)
    binary = Path(config["native_binary"])
    info = binary.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("native desktop binary is not trusted")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != config["native_binary_sha256"]:
        raise PermissionError("native desktop binary version changed")
    uid = config["desktop_uid"]
    account = pwd.getpwuid(uid)
    if uid == 0:
        raise PermissionError("desktop actions cannot run as root")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise PermissionError("native desktop could not drop escalation privileges")
    # This gate handles just one request. Drop the wrapper itself as well as
    # its child; even a compromised app response cannot regain root access.
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(uid)
    base = Path(f"/run/user/{uid}/kai-computer-ax") / config["computer_id"]
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = base / hashlib.sha256(scope.encode()).hexdigest()
    home.mkdir(mode=0o700, exist_ok=True)
    # Exactly one execution lease is live on this computer. Drop only expired
    # namespaces of this adapter, as the unprivileged desktop user.
    for old in base.iterdir():
        if old != home and re.fullmatch(r"[a-f0-9]{64}", old.name):
            if old.is_symlink():
                old.unlink()
            elif old.is_dir():
                shutil.rmtree(old)
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": account.pw_dir,
        "DISPLAY": config["display"],
        "XAUTHORITY": config["xauthority"],
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
        "AGENT_DESKTOP_HOME": str(home),
    }

    def interrupted(_signal: int, _frame: object) -> None:
        raise SystemExit(129)

    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryFile() as output:
        parent = os.getpid()

        def child_guard() -> None:
            # The native headless binary does not spawn application processes.
            # A killed SSH wrapper must not leave its action running behind it.
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
                os._exit(125)

        child = subprocess.Popen(
            [str(binary), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=child_guard,
        )
        try:
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                return uncertain("TIMEOUT", "Native desktop response timed out")
            if output.tell() > MAX_OUTPUT:
                return uncertain("OUTPUT_TOO_LARGE", "Use a skeleton, root or smaller max_depth")
            output.seek(0)
            value = json.loads(output.read(MAX_OUTPUT))
            if not isinstance(value, dict):
                raise ValueError("native desktop response is malformed")
            envelope = cast(dict[str, Any], value)
            if not isinstance(envelope.get("ok"), bool):
                raise ValueError("native desktop response is malformed")
            return envelope
        finally:
            # Also stop any helper in the command's group; a cancelled SSH
            # request must finish draining before the owner gets control.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
