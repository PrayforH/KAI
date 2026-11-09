"""Own one Bot desktop slot inside a computer VM.

The SSH gate is the only caller: it runs as guest root and has already checked
that the request belongs to an authorized computer. This module still validates
every field, because a compromised gate must not become a user-provisioning or
arbitrary-unit primitive.
"""

import hashlib
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SESSIONS = Path("/etc/kai-desktop/sessions")
BOTS = Path("/home/kai-bots")
# Root-owned 0711 parent: reachable by slot users, never listable.
RUNTIME = Path("/run/kai-bots")
UNIT = "desktop-session@"
UID_BASE = 28100
UID_SPAN = 800

DESKTOP_ID = re.compile(r"^desktop_[0-9a-f]{24}$")
UNIX_USER = re.compile(r"^kai-bot-[0-9a-f]{8}$")
DISPLAY = re.compile(r"^(1[0-9][0-9])$")
OPS = ("ensure", "start", "stop", "status")
ENVIRONMENT = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "LANG": "C.UTF-8"}


def _slot_key(desktop_id: str) -> str:
    if not DESKTOP_ID.fullmatch(desktop_id):
        raise ValueError("desktop id is invalid")
    return hashlib.sha256(desktop_id.encode()).hexdigest()


def _validate_account(account: pwd.struct_passwd, home: str) -> pwd.struct_passwd:
    if (
        not UID_BASE <= account.pw_uid < UID_BASE + UID_SPAN
        or account.pw_gid != account.pw_uid
        or account.pw_dir != home
    ):
        raise ValueError("desktop account does not match the slot")
    return account


def slot_account(desktop_id: str) -> pwd.struct_passwd:
    key = _slot_key(desktop_id)
    return _validate_account(pwd.getpwnam(f"kai-bot-{key[:8]}"), str(BOTS / key[:16]))


@dataclass(frozen=True)
class SessionPlan:
    desktop_id: str
    unix_user: str
    display_number: str
    home: str
    profile: str
    workspace: str
    vnc_socket: str
    runtime: str

    def environment(self) -> str:
        rows = (
            ("DESKTOP_ID", self.desktop_id),
            ("DISPLAY_NUMBER", self.display_number),
            ("UNIX_USER", self.unix_user),
            ("HOME", self.home),
            ("PROFILE", self.profile),
            ("WORKSPACE", self.workspace),
            ("VNC_SOCKET", self.vnc_socket),
        )
        return "".join(f"{key}={value}\n" for key, value in rows)


def plan(
    desktop_id: str,
    unix_user: str,
    display: str,
    profile: str,
    workspace: str,
    vnc_socket: str,
) -> SessionPlan:
    """Validate a caller's slot description and derive the only allowed paths."""
    key = _slot_key(desktop_id)
    if not UNIX_USER.fullmatch(unix_user):
        raise ValueError("desktop unix user is invalid")
    if display.startswith(":"):
        display = display[1:]
    if not DISPLAY.fullmatch(display):
        raise ValueError("desktop display is invalid")
    short = key[:16]
    if profile != f"{BOTS}/{short}/chromium" or workspace != f"{BOTS}/{short}/workspace":
        raise ValueError("desktop profile or workspace escaped its bot directory")
    if vnc_socket != f"{RUNTIME}/{desktop_id}/vnc.sock":
        raise ValueError("desktop vnc socket escaped its runtime directory")
    return SessionPlan(
        desktop_id=desktop_id,
        unix_user=unix_user,
        display_number=display,
        home=f"{BOTS}/{short}",
        profile=profile,
        workspace=workspace,
        vnc_socket=vnc_socket,
        runtime=f"{RUNTIME}/{desktop_id}",
    )


def apply(op: str, session: SessionPlan) -> str:
    if op == "ensure":
        _prepare(session)
    elif op == "start":
        if not (SESSIONS / f"{session.desktop_id}.env").is_file():
            raise ValueError("desktop has no prepared session")
        _systemctl("start", _unit(session))
    elif op == "stop":
        _systemctl("stop", _unit(session))
        _systemctl("disable", _unit(session))
        # A stopped slot must not leave a stale RFB socket or X cookie behind.
        runtime = Path(session.runtime)
        if runtime.parent == RUNTIME and runtime.name == session.desktop_id:
            shutil.rmtree(runtime, ignore_errors=True)
    elif op != "status":
        raise ValueError("desktop operation is invalid")
    if op in {"ensure", "start"}:
        return _wait_for_display(session)
    return status(session)


def _wait_for_display(session: SessionPlan) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if status(session) == "active":
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                    channel.settimeout(1)
                    channel.connect(session.vnc_socket)
                    if channel.recv(4, socket.MSG_WAITALL) == b"RFB ":
                        return "active"
            except OSError:
                pass
        time.sleep(0.2)
    raise RuntimeError("desktop did not expose an RFB display")


def status(session: SessionPlan) -> str:
    result = subprocess.run(
        ["/usr/bin/systemctl", "is-active", _unit(session)],
        capture_output=True,
        text=True,
        env=ENVIRONMENT,
        check=False,
    )
    return result.stdout.strip() or "inactive"


def _unit(session: SessionPlan) -> str:
    return f"{UNIT}{session.desktop_id}"


def _prepare(session: SessionPlan) -> None:
    BOTS.mkdir(mode=0o755, parents=True, exist_ok=True)
    RUNTIME.mkdir(mode=0o711, parents=True, exist_ok=True)
    _ensure_account(session)
    account = pwd.getpwnam(session.unix_user)
    owner = (account.pw_uid, account.pw_gid)
    home = _open_directory(Path(session.home), owner)
    try:
        for name in ("chromium", "workspace"):
            os.close(_open_directory(Path(name), owner, parent=home))
    finally:
        os.close(home)
    SESSIONS.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_environment(session)
    _systemctl("enable", _unit(session))
    if status(session) != "active":
        _systemctl("start", _unit(session))


def _open_directory(
    path: Path, owner: tuple[int, int], *, parent: int | None = None
) -> int:
    try:
        os.mkdir(path, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if info.st_uid == 0:
            # A newly created directory is still root-owned after a crash.
            os.fchown(fd, *owner)
        elif (info.st_uid, info.st_gid) != owner:
            raise PermissionError("desktop directory belongs to another account")
        os.fchmod(fd, 0o700)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _write_environment(session: SessionPlan) -> None:
    target = SESSIONS / f"{session.desktop_id}.env"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(session.environment())


def _ensure_account(session: SessionPlan) -> None:
    try:
        account = pwd.getpwnam(session.unix_user)
    except KeyError:
        account = None
    if account is not None:
        # A slot is addressed by its own hash-derived name; a changed UID would
        # silently hand another Bot's files to this one.
        _validate_account(account, session.home)
        return
    identifier = _uid_for_new_account(session)
    subprocess.run(
        ["/usr/sbin/groupadd", "--gid", str(identifier), session.unix_user],
        check=True,
        env=ENVIRONMENT,
    )
    subprocess.run(
        [
            "/usr/sbin/useradd",
            "--uid",
            str(identifier),
            "--gid",
            str(identifier),
            "--home-dir",
            session.home,
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            session.unix_user,
        ],
        check=True,
        env=ENVIRONMENT,
    )


def _uid_for_new_account(session: SessionPlan) -> int:
    try:
        home = Path(session.home).lstat()
    except FileNotFoundError:
        return _allocate_uid(session.desktop_id)
    # A rebuilt guest must recreate the account with the persisted directory's
    # UID. Allocation order may have changed if two slot hashes collided.
    if (
        not stat.S_ISDIR(home.st_mode)
        or stat.S_IMODE(home.st_mode) != 0o700
        or not UID_BASE <= home.st_uid < UID_BASE + UID_SPAN
        or home.st_gid != home.st_uid
        or _taken(home.st_uid)
    ):
        raise PermissionError("desktop home cannot restore a private account")
    return home.st_uid


def _allocate_uid(desktop_id: str) -> int:
    start = int(desktop_id[-6:], 16) % UID_SPAN
    for offset in range(UID_SPAN):
        candidate = UID_BASE + (start + offset) % UID_SPAN
        if not _taken(candidate):
            return candidate
    raise ValueError("no free desktop uid")


def _taken(identifier: int) -> bool:
    return any(
        account.pw_uid == identifier
        for account in pwd.getpwall()
        if account.pw_uid >= UID_BASE
    ) or _group_gid_taken(identifier)


def _group_gid_taken(identifier: int) -> bool:
    import grp

    return any(group.gr_gid == identifier for group in grp.getgrall())


def _systemctl(verb: str, unit: str) -> None:
    subprocess.run(["/usr/bin/systemctl", verb, unit], check=True, env=ENVIRONMENT)


def main(argv: list[str]) -> str:
    if len(argv) != 7 or argv[0] not in OPS:
        raise ValueError("desktop session request is invalid")
    return apply(argv[0], plan(*argv[1:]))


if __name__ == "__main__":
    try:
        print(main(sys.argv[1:]))
    except Exception as error:  # noqa: BLE001 - the gate maps this to a refusal
        print(f"desktop control refused: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(125) from None
