"""Small privileged execution gate installed inside a Computer image.

Only execd's authenticated control plane invokes this program as root. Model
commands run as an unprivileged UID inside a lease-owned PID/mount namespace.
Every activation drains the old namespace before opening the next epoch,
including detached daemons, nested namespaces and surviving process threads.
The process never imports application packages or credentials from HOME.

The CLI consumes a one-use, root-only spool file, so environment credentials
and command bodies do not appear in execd's command-line log. This file is
standalone stdlib code and is run with Python's isolated (-I) flag.
"""

from __future__ import annotations

import base64
import ctypes
import fcntl
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

ROOT = Path("/var/lib/kai-computer")
AGENT_UID = 28000
AGENT_GID = 28000
HOME = "/home/kai"
MAX_SPEC_BYTES = 40 * 1024 * 1024


@dataclass(frozen=True)
class Token:
    epoch: int
    lease_id: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> Token:
        epoch, identifier = value.get("epoch"), value.get("lease_id")
        if (
            type(epoch) is not int
            or epoch < 1
            or not isinstance(identifier, str)
            or re.fullmatch(r"computer_lease_[a-f0-9]{32}", identifier) is None
        ):
            raise ValueError("invalid execution token")
        return cls(epoch, identifier)


def _expiration(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("invalid execution expiration")
    moment = float(value)
    if not math.isfinite(moment) or not time.time() < moment <= time.time() + 3600:
        raise ValueError("execution expiration is outside the allowed interval")
    return moment


class Guard:
    root: Path
    uid: int
    gid: int
    home: str

    def __init__(
        self, root: Path = ROOT, *, uid: int = AGENT_UID, gid: int = AGENT_GID, home: str = HOME
    ) -> None:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise PermissionError("computer supervisor requires its privileged Linux control plane")
        if uid <= 0 or gid <= 0:
            raise ValueError("agent identity must be unprivileged")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise PermissionError("computer control directory is not private")
        self.root, self.uid, self.gid, self.home = root, uid, gid, home
        (root / "spool").mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def _locked(self) -> Generator[None]:
        fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads((self.root / "state.json").read_bytes())
        except FileNotFoundError:
            return {"epoch": 0, "active": False}
        if not isinstance(value, dict):
            raise RuntimeError("invalid computer control state")
        return cast(dict[str, Any], value)

    def _save(self, state: Mapping[str, Any]) -> None:
        path = self.root / "state.next"
        fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(dict(state), output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(path, self.root / "state.json")

    @staticmethod
    def _same(state: Mapping[str, Any], token: Token) -> bool:
        return state.get("epoch") == token.epoch and state.get("lease_id") == token.lease_id

    def _live(self, state: Mapping[str, Any], token: Token) -> None:
        if not self._same(state, token) or not state.get("active"):
            raise RuntimeError("execution lease was closed or superseded")
        if float(state.get("expires_at", 0)) <= time.time():
            raise RuntimeError("execution lease expired")

    def _namespace_fd(self, state: Mapping[str, Any], kind: str = "pid") -> int | None:
        value = state.get("namespace")
        if not isinstance(value, dict):
            return None
        detail = cast(dict[str, Any], value)
        boot_id = detail.get("boot_id")
        if boot_id is not None:
            if not isinstance(boot_id, str) or not _valid_boot_id(boot_id):
                raise RuntimeError("execution kernel identity is invalid")
            if boot_id != _kernel_boot_id():
                # PID/start time and namespace inode numbers can repeat after
                # a kernel restart. No process from that old kernel survives.
                return None
        pid = detail.get("pid")
        if type(pid) is not int or pid <= 1 or _process_start(pid) != detail.get("start"):
            return None
        try:
            fd = os.open(f"/proc/{pid}/ns/{kind}", os.O_RDONLY)
        except OSError:
            # Older kernels can return EACCES/EINVAL while namespace init is
            # exiting. Recheck its lifetime; an observation failure alone is
            # never proof that the execution stopped.
            if _process_start(pid) != detail.get("start"):
                return None
            raise
        if os.fstat(fd).st_ino != detail.get(kind + "_inode"):
            os.close(fd)
            return None
        if boot_id is None:
            # Legacy metadata may prove absence, but cannot grant entry into a
            # matching live namespace without knowing which kernel created it.
            os.close(fd)
            raise RuntimeError("legacy execution identity requires physical recovery")
        return fd

    def _namespace_live(self, state: Mapping[str, Any]) -> None:
        fd = self._namespace_fd(state)
        if fd is None:
            raise RuntimeError("execution namespace has exited")
        os.close(fd)

    def _drain(self, state: Mapping[str, Any]) -> None:
        # Its init observes the closed/replaced token and exits. Linux then
        # kills and reaps every member, including detached daemons, threads and
        # nested namespaces. No numeric-PID signal/reuse race or pidfd is needed.
        deadline = time.monotonic() + 10
        while True:
            try:
                fd = self._namespace_fd(state)
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("old execution exit could not be confirmed") from None
                time.sleep(0.02)
                continue
            if fd is None:
                return
            os.close(fd)
            if time.monotonic() >= deadline:
                raise RuntimeError("old execution has not exited; computer remains fenced")
            time.sleep(0.02)

    def _spawn_namespace(self, state: dict[str, Any], token: Token) -> None:
        ready = self.root / ("namespace-" + token.lease_id + ".json")
        ready.unlink(missing_ok=True)
        os.posix_spawnp(
            "unshare",
            [
                "unshare",
                "--mount",
                "--propagation",
                "private",
                "--pid",
                "--fork",
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "namespace",
                str(self.root),
                str(self.uid),
                str(self.gid),
                self.home,
                str(token.epoch),
                token.lease_id,
            ],
            {"PATH": "/usr/local/bin:/usr/bin:/bin"},
            setsid=True,
            file_actions=[
                (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                (
                    os.POSIX_SPAWN_OPEN,
                    2,
                    str(self.root / "namespace-error.log"),
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,
                ),
            ],
        )
        deadline = time.monotonic() + 3
        while not ready.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("execution namespace did not initialize")
            time.sleep(0.01)
        state["namespace"] = json.loads(ready.read_bytes())
        ready.unlink()
        self._namespace_live(state)

    def activate(self, token: Token, expires_at: float) -> dict[str, Any]:
        expires_at = _expiration(expires_at)
        with self._locked():
            state = self._read()
            if self._same(state, token):
                self._live(state, token)
                self._namespace_live(state)
                return self._read()
            if token.epoch <= int(state["epoch"]):
                raise RuntimeError("execution epoch was superseded")
            previous = state
            state = {
                "epoch": token.epoch,
                "lease_id": token.lease_id,
                "expires_at": expires_at,
                "active": False,
                "opening": True,
            }
            # Publish the fence before draining; a failed drain cannot reopen it.
            self._save(state)
            self._drain(previous)
            self._spawn_namespace(state, token)
            state["active"], state["opening"] = True, False
            self._save(state)
            return state

    def renew(self, token: Token, expires_at: float) -> dict[str, Any]:
        expires_at = _expiration(expires_at)
        with self._locked():
            state = self._read()
            self._live(state, token)
            state["expires_at"] = max(expires_at, float(state["expires_at"]))
            self._save(state)
            self._namespace_live(state)
            return self._read()

    def deactivate(self, token: Token) -> dict[str, Any]:
        with self._locked():
            state = self._read()
            if not self._same(state, token):
                raise RuntimeError("execution epoch was superseded")
            state["active"] = False
            self._save(state)
            self._drain(state)
            return state

    def namespace(self, token: Token) -> None:
        # Capture IDs in the computer's procfs before mounting this execution's
        # own procfs. The host controller verifies both namespace inodes and the
        # init's start time before allowing a process to join it.
        pid = int(os.readlink("/proc/self"))
        detail = {
            "boot_id": _kernel_boot_id(),
            "pid": pid,
            "start": _process_start(pid),
            "pid_inode": os.stat("/proc/self/ns/pid").st_ino,
            "mnt_inode": os.stat("/proc/self/ns/mnt").st_ino,
        }
        _mount_proc()
        ready = self.root / ("namespace-" + token.lease_id + ".json")
        temporary = ready.with_suffix(".next")
        temporary.write_text(json.dumps(detail))
        temporary.chmod(0o600)
        os.replace(temporary, ready)
        opening_deadline = time.monotonic() + 3
        while True:
            state = self._read()
            if not self._same(state, token) or float(state.get("expires_at", 0)) <= time.time():
                return
            if not state.get("active") and not (
                state.get("opening") and time.monotonic() < opening_deadline
            ):
                return
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                pass
            time.sleep(0.05)

    def execute(self, token: Token, spec: Mapping[str, Any]) -> int:
        argv, environment = spec.get("argv"), spec.get("environment", {})
        cwd, timeout = spec.get("cwd"), spec.get("timeout_seconds", 30)
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or "\0" in arg for arg in cast(list[object], argv))
            or not isinstance(environment, dict)
            or any(
                not isinstance(k, str)
                or not isinstance(v, str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) is None
                or "\0" in v
                for k, v in cast(dict[object, object], environment).items()
            )
            or not isinstance(cwd, str)
            or not cwd.startswith("/")
            or not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 3600
        ):
            raise ValueError("invalid execution request")
        raw_input = spec.get("stdin_base64")
        content = base64.b64decode(raw_input, validate=True) if isinstance(raw_input, str) else None
        with self._locked():
            state = self._read()
            self._live(state, token)
            self._namespace_live(state)
            pid_ns, mount_ns = self._namespace_fd(state), self._namespace_fd(state, "mnt")
            if pid_ns is None or mount_ns is None:
                if pid_ns is not None:
                    os.close(pid_ns)
                if mount_ns is not None:
                    os.close(mount_ns)
                raise RuntimeError("execution namespace has exited")
            original_pid = os.open("/proc/self/ns/pid", os.O_RDONLY)
            original_mount = os.open("/proc/self/ns/mnt", os.O_RDONLY)
            try:
                _setns(mount_ns, 0x00020000)
                _setns(pid_ns, 0x20000000)
                process = subprocess.Popen(
                    cast(list[str], argv),
                    cwd=cwd,
                    env={
                        "PATH": f"{self.home}/.local/bin:{self.home}/bin:"
                        "/usr/local/bin:/usr/bin:/bin",
                        "LANG": "C.UTF-8",
                        **cast(dict[str, str], environment),
                        "HOME": self.home,
                    },
                    user=self.uid,
                    group=self.gid,
                    extra_groups=[],
                    stdin=subprocess.PIPE if content is not None else subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                )
            finally:
                _setns(original_mount, 0x00020000)
                _setns(original_pid, 0x20000000)
                for fd in (original_mount, original_pid, mount_ns, pid_ns):
                    os.close(fd)
        if content is not None:
            # An uncooperative child must not block the root watchdog on stdin.
            threading.Thread(target=_feed, args=(process, content), daemon=True).start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with self._locked():
                state = self._read()
                if self._same(state, token):
                    state["active"] = False
                    self._save(state)
                    # Namespace init cannot finish exiting until an external
                    # parent reaps this direct child. Reap before waiting for
                    # the namespace itself, otherwise timeout cleanup deadlocks.
                    process.wait(timeout=5)
                    self._drain(state)
                    if float(state.get("expires_at", 0)) > time.time():
                        state["opening"] = True
                        self._save(state)
                        self._spawn_namespace(state, token)
                        state["active"], state["opening"] = True, False
                        self._save(state)
            process.wait(timeout=5)
            return 124
        return code if code >= 0 else 128 - code

    def initialize(self, token: Token, expires_at: float) -> dict[str, Any]:
        """Bootstrap HOME under the same physical fence as ordinary execution."""

        self.activate(token, expires_at)
        try:
            with self._locked():
                self._live(self._read(), token)
                home = Path(self.home)
                if not stat.S_ISDIR(home.lstat().st_mode):
                    raise PermissionError("computer HOME must be a real directory")
                os.chown(home, self.uid, self.gid)
            code = self.execute(
                token,
                {
                    "argv": [
                        sys.executable,
                        "-I",
                        "-c",
                        "from pathlib import Path; import sys; root=Path(sys.argv[1]); "
                        "[(root/p).mkdir(parents=True,exist_ok=True) "
                        "for p in ['workspace','bin','.local/bin','.config/chromium']]",
                        self.home,
                    ],
                    "cwd": "/",
                    "timeout_seconds": 30,
                },
            )
            if code != 0:
                raise RuntimeError("computer HOME initialization failed")
        finally:
            closed = self.deactivate(token)
        return {**closed, "initialized": True, "agent_uid": self.uid}

    def consume(self, path: Path) -> dict[str, Any]:
        if (
            path.parent != self.root / "spool"
            or re.fullmatch(r"[a-f0-9]{32}\.json", path.name) is None
        ):
            raise ValueError("execution request must use a private spool file")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise PermissionError("execution request is not private")
            with os.fdopen(fd, "rb", closefd=False) as input_file:
                content = input_file.read(MAX_SPEC_BYTES + 1)
            if len(content) > MAX_SPEC_BYTES:
                raise ValueError("execution request exceeds size limit")
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError("execution request must be an object")
            return cast(dict[str, Any], value)
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)


def _valid_boot_id(value: str) -> bool:
    return re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value) is not None


def _kernel_boot_id() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not _valid_boot_id(value):
        raise RuntimeError("current kernel identity could not be confirmed")
    return value


def _process_start(pid: object) -> str | None:
    if type(pid) is not int:
        return None
    try:
        rest = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if rest[0] == "Z" else rest[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def _setns(fd: int, kind: int) -> None:
    function = ctypes.CDLL(None, use_errno=True).setns
    function.argtypes, function.restype = [ctypes.c_int, ctypes.c_int], ctypes.c_int
    if function(fd, kind) != 0:
        raise OSError(ctypes.get_errno(), "execution namespace could not be joined")


def _mount_proc() -> None:
    function = ctypes.CDLL(None, use_errno=True).mount
    function.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    function.restype = ctypes.c_int
    if function(b"proc", b"/proc", b"proc", 2 | 4 | 8, None) != 0:
        raise OSError(ctypes.get_errno(), "execution procfs could not be mounted")


def _feed(process: subprocess.Popen[bytes], content: bytes) -> None:
    assert process.stdin is not None
    try:
        process.stdin.write(content)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "prepare":
        guard = Guard()
        for path in (guard.root / "spool").iterdir():
            if re.fullmatch(r"[a-f0-9]{32}\.json", path.name) is not None:
                path.unlink()
        return 0
    if len(sys.argv) == 8 and sys.argv[1] == "namespace":
        guard = Guard(
            Path(sys.argv[2]), uid=int(sys.argv[3]), gid=int(sys.argv[4]), home=sys.argv[5]
        )
        guard.namespace(Token(int(sys.argv[6]), sys.argv[7]))
        return 0
    if len(sys.argv) != 3 or sys.argv[1] not in {"init", "activate", "renew", "deactivate", "exec"}:
        raise ValueError("expected supervisor action and private request path")
    guard = Guard()
    spec = guard.consume(Path(sys.argv[2]))
    token = Token.parse(spec)
    if sys.argv[1] == "exec":
        return guard.execute(token, spec)
    if sys.argv[1] == "deactivate":
        result = guard.deactivate(token)
    elif sys.argv[1] == "init":
        result = guard.initialize(token, _expiration(spec.get("expires_at")))
    else:
        expires_at = _expiration(spec.get("expires_at"))
        result = (
            guard.activate(token, expires_at)
            if sys.argv[1] == "activate"
            else guard.renew(token, expires_at)
        )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, PermissionError, RuntimeError, OSError):
        print("Computer execution was refused; check its lease and control state.", file=sys.stderr)
        raise SystemExit(125) from None
