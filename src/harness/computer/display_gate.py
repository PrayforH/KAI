"""Forced SSH command: relay one private desktop's Unix RFB socket.

The viewer key is forced to this command, so a browser can never name a
socket, path, user or program. With no client command the historical
``kai-desktop`` socket is served; a single ``desktop_<24 hex>`` argument
selects one Bot slot under ``/run/kai-bots``. The command runs as root only
long enough to validate the slot, then drops to the socket owner before it
connects; the RFB relay itself never holds root.
"""

import contextlib
import ctypes
import os
import re
import socket
import stat
import sys
import threading

LEGACY_SOCKET = "/run/kai-desktop/vnc.sock"
LEGACY_UID = 28001
RUNTIME = "/run/kai-bots"
RUNTIME_UID = 0
BOT_UID_BASE = 28100
BOT_UID_SPAN = 800
DESKTOP_ID = re.compile(r"^desktop_[0-9a-f]{24}$")
PR_SET_NO_NEW_PRIVS = 38


def requested_desktop(environment: dict[str, str] | None = None) -> str | None:
    """The only client input this gate accepts: an empty or slot command."""

    raw = (environment if environment is not None else os.environ).get("SSH_ORIGINAL_COMMAND")
    if raw is None or raw == "":
        return None
    if not DESKTOP_ID.fullmatch(raw):
        raise ValueError("desktop request is invalid")
    return raw


def trusted_runtime(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != RUNTIME_UID or info.st_mode & 0o022:
        raise PermissionError("desktop runtime is not trusted")


def trusted_slot(info: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or not BOT_UID_BASE <= info.st_uid < BOT_UID_BASE + BOT_UID_SPAN
        or info.st_mode & 0o077
    ):
        raise PermissionError("desktop slot is not trusted")


def trusted_socket(info: os.stat_result, owner: int | None = None) -> None:
    if not stat.S_ISSOCK(info.st_mode):
        raise PermissionError("desktop socket is not a socket")
    if owner is None:
        if info.st_uid != LEGACY_UID:
            raise PermissionError("desktop socket owner is not the desktop user")
    elif info.st_uid != owner:
        raise PermissionError("desktop socket owner changed")


def socket_path(desktop: str | None) -> str:
    if desktop is None:
        return LEGACY_SOCKET
    return f"{RUNTIME}/{desktop}/vnc.sock"


def open_socket(desktop: str | None) -> tuple[int, int, int]:
    """Inspect the selected socket before dropping to its owner's UID."""
    if desktop is None:
        fd = os.open(LEGACY_SOCKET, os.O_PATH | os.O_NOFOLLOW)
        info = os.fstat(fd)
        trusted_socket(info)
        return fd, info.st_uid, info.st_gid
    root = os.open(RUNTIME, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        trusted_runtime(os.fstat(root))
        slot = os.open(desktop, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    finally:
        os.close(root)
    try:
        slot_info = os.fstat(slot)
        trusted_slot(slot_info)
        fd = os.open("vnc.sock", os.O_PATH | os.O_NOFOLLOW, dir_fd=slot)
    finally:
        os.close(slot)
    info = os.fstat(fd)
    trusted_socket(info, slot_info.st_uid)
    return fd, info.st_uid, info.st_gid


def drop_to(uid: int, gid: int) -> None:
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise PermissionError("private desktop transport could not drop privileges")
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    if (os.geteuid(), os.getegid()) != (uid, gid):
        raise PermissionError("private desktop transport did not change user")


def main() -> None:
    if sys.platform != "linux" or os.geteuid() != 0 or len(sys.argv) != 1:
        raise PermissionError("private desktop transport required")
    desktop = requested_desktop()
    fd, uid, gid = open_socket(desktop)
    os.close(fd)
    # The validated path is fixed by the slot id; after the drop the process
    # can only reach a socket owned by that same Bot account.
    path = socket_path(desktop)
    drop_to(uid, gid)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(path)

        def upstream() -> None:
            try:
                while data := os.read(0, 65536):
                    channel.sendall(data)
            finally:
                with contextlib.suppress(OSError):
                    channel.shutdown(socket.SHUT_WR)

        threading.Thread(target=upstream, daemon=True).start()
        while data := channel.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
