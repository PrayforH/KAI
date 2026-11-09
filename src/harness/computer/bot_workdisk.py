"""Mount the Bot data export and import old guest directories during maintenance."""

import fcntl
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = Path("/etc/kai-computer/bot-workdisk")
EXPORT = Path("/run/kai-bot-workdisk")
BOTS = Path("/home/kai-bots")
TAG = "kai_bots"
MARKER = ".kai-workdisk"


def _trusted(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not valid_type(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("Bot workdisk path is not trusted")


def _identity() -> str | None:
    try:
        _trusted(CONFIG)
    except FileNotFoundError:
        return None
    identifier = CONFIG.read_text().strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", identifier) is None:
        raise ValueError("Bot workdisk identity is invalid")
    return identifier


def _mounted(path: Path) -> tuple[list[str], list[str]]:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        mount_id = next(
            line.split(":", 1)[1].strip()
            for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
            if line.startswith("mnt_id:")
        )
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            before, separator, after = line.partition(" - ")
            fields = before.split()
            if separator and fields[0] == mount_id:
                return fields, after.split()
        raise RuntimeError("Bot workdisk mount cannot be identified")
    finally:
        os.close(fd)


def _is_export(mount: tuple[list[str], list[str]], path: Path, root: str) -> bool:
    fields, filesystem = mount
    return (
        fields[3:5] == [root, str(path)]
        and {"rw", "nosuid", "nodev"} <= set(fields[5].split(","))
        and filesystem[:2] == ["9p", TAG]
    )


def _check_marker(path: Path, identifier: str) -> None:
    _trusted(path, directory=True)
    marker = path / MARKER
    _trusted(marker)
    if marker.read_text().strip() != identifier:
        raise PermissionError("Bot workdisk identity does not match the guest")


def require(*, expected: str | None = None) -> None:
    identifier = _identity()
    if expected is not None and identifier != expected:
        raise PermissionError("Bot workdisk identity does not match the node")
    if identifier is None:
        return
    if not _is_export(_mounted(BOTS), BOTS, "/data"):
        raise PermissionError("Bot workdisk is not mounted")
    _check_marker(BOTS, identifier)


def mount(*, initialize: bool = False) -> None:
    identifier = _identity()
    if identifier is None:
        raise PermissionError("Bot workdisk is not configured")
    _trusted(BOTS, directory=True)
    current = _mounted(BOTS)
    if current[0][4] == str(BOTS):
        require(expected=identifier)
        return
    EXPORT.mkdir(mode=0o700, exist_ok=True)
    export_mount = _mounted(EXPORT)
    if export_mount[0][4] != str(EXPORT):
        _trusted(EXPORT, directory=True)
        subprocess.run([
            "/usr/bin/mount", "-t", "9p",
            "-o", "trans=virtio,version=9p2000.L,cache=none,nodev,nosuid",
            TAG, str(EXPORT),
        ], check=True)
        export_mount = _mounted(EXPORT)
    if not _is_export(export_mount, EXPORT, "/"):
        raise PermissionError("Bot workdisk has the wrong export mount")

    data = EXPORT / "data"
    if initialize and not os.path.lexists(data):
        # The host pauses Workers before booting the maintenance seed. Stop
        # any old desktop units that were enabled on the guest system disk.
        subprocess.run([
            "/usr/bin/systemctl", "stop", "desktop-session@*.service",
        ], check=True)
        os.chown(EXPORT, 0, 0)
        os.chmod(EXPORT, 0o700)
        with tempfile.TemporaryDirectory(prefix=".import-", dir=EXPORT) as temporary:
            staging = Path(temporary)
            subprocess.run(["/usr/bin/cp", "-a", f"{BOTS}/.", str(staging)], check=True)
            marker = staging / MARKER
            with marker.open("x") as stream:
                stream.write(identifier + "\n")
            marker.chmod(0o600)
            os.sync()
            staging.rename(data)
            os.sync()
    _check_marker(data, identifier)
    subprocess.run(["/usr/bin/mount", "--bind", str(data), str(BOTS)], check=True)
    require(expected=identifier)


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        require()
    elif len(sys.argv) == 2 and sys.argv[1] in {"mount", "initialize"}:
        lock = os.open("/run/kai-bot-workdisk.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            mount(initialize=sys.argv[1] == "initialize")
        finally:
            os.close(lock)
    else:
        raise SystemExit("expected check, mount or initialize")
