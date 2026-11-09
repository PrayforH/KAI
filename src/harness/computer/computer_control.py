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
    if not DESKTOP_ID.fullmatch(desktop_id):
        raise ValueError("desktop id is invalid")
    if not UNIX_USER.fullmatch(unix_user):
        raise ValueError("desktop unix user is invalid")
    if display.startswith(":"):
        display = display[1:]
    if not DISPLAY.fullmatch(display):
        raise ValueError("desktop display is invalid")
    short = hashlib.sha256(desktop_id.encode()).hexdigest()[:16]
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
    owner = (pwd.getpwnam(session.unix_user).pw_uid, pwd.getpwnam(session.unix_user).pw_gid)
    for path in (Path(session.home), Path(session.profile), Path(session.workspace)):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(path, *owner)
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o700)
    SESSIONS.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_environment(session)
    _systemctl("enable", _unit(session))
    if status(session) != "active":
        _systemctl("start", _unit(session))


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
        if not UID_BASE <= account.pw_uid < UID_BASE + UID_SPAN:
            raise ValueError("desktop account is outside the bot UID range")
        return
    identifier = _allocate_uid(session.desktop_id)
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
