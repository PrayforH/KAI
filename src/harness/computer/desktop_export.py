"""Copy an explicitly published desktop file into the existing task file plane.

Installed as a root-owned, stdlib-only guest module. Source paths are relative
only to the desktop outbox; destination paths are fixed under outputs/desktop.
No profile access, shell commands, arbitrary HOME paths or file bytes in JSON.
"""

import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

OUTBOX = Path("/var/lib/kai-desktop/deliver")
WORKSPACE = Path("/home/kai/workspace")
MAX_BYTES = 64 * 1024**2
CHUNK = 1024**2
DESKTOP_UID = 28001
TASK_UID = 28000


def arguments(payload: dict[str, Any]) -> tuple[list[str], str]:
    if payload.keys() - {"path", "name"}:
        raise ValueError("desktop export accepts only path and name")
    path = payload.get("path")
    if not isinstance(path, str) or not 1 <= len(path) <= 1024:
        raise ValueError("use a path relative to the desktop deliver directory")
    parts = path.split("/")
    if len(parts) > 32 or any(
        not p or p in {".", ".."} or any(ord(c) < 32 for c in p) for p in parts
    ):
        raise ValueError("desktop export path is invalid")
    name = payload.get("name", parts[-1])
    if (
        not isinstance(name, str)
        or not 1 <= len(name.encode()) <= 240
        or name in {".", ".."}
        or "/" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError("desktop export name must be a filename")
    return parts, name


@contextmanager
def directory(root: Path, parts: list[str], *, create: bool = False):
    # Walk all ancestors as well: no symlink in a HOME/workspace prefix may
    # turn this privileged copy into an arbitrary source or destination.
    with ExitStack() as stack:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, fd)
        for name in [*root.parts[1:], *parts]:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileExistsError:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            else:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            stack.callback(os.close, child)
            fd = child
        yield fd


def regular(fd: int, uid: int) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1:
        raise ValueError("desktop export requires an owned regular file without links")
    if not 0 <= info.st_size <= MAX_BYTES:
        raise ValueError("desktop export file exceeds 64 MiB")
    return info


def signature(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def digest(fd: int, check: Callable[[], None]) -> str:
    value = hashlib.sha256()
    while data := os.read(fd, CHUNK):
        check()
        value.update(data)
    return value.hexdigest()


@contextmanager
def task_writer(check: Callable[[], None]):
    """Use the workdisk's task identity, never root's 9P credentials.

    The gate runs in a dedicated, single-threaded process with trusted code.
    Only lease checks briefly regain the saved effective UID; no task code or
    executable is loaded while the privileged source descriptor is held.
    """
    if os.geteuid() != 0:
        if os.geteuid() != TASK_UID:
            raise PermissionError("desktop export requires the task writer identity")
        yield check
        return
    groups, gid = os.getgroups(), os.getegid()

    def root_check() -> None:
        os.seteuid(0)
        try:
            check()
        finally:
            os.seteuid(TASK_UID)

    try:
        os.setgroups([])
        os.setegid(TASK_UID)
        os.seteuid(TASK_UID)
        yield root_check
    finally:
        os.seteuid(0)
        os.setegid(gid)
        os.setgroups(groups)


def copy_file(
    payload: dict[str, Any], check: Callable[[], None], *, workspace: Path | None = None,
) -> dict[str, Any]:
    parts, name = arguments(payload)
    check()
    with directory(workspace or OUTBOX, parts[:-1]) as source_dir:
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_dir)
    try:
        if workspace is not None:
            uid = os.geteuid()
            return copy_to_workspace(
                source, name, regular(source, uid), check, workspace=workspace, uid=uid,
            )
        before = regular(source, DESKTOP_UID)
        with task_writer(check) as guarded_check:
            return copy_to_workspace(
                source, name, before, guarded_check, workspace=WORKSPACE, uid=TASK_UID,
            )
    finally:
        os.close(source)


def copy_to_workspace(
    source: int, name: str, before: os.stat_result, check: Callable[[], None],
    *, workspace: Path, uid: int,
) -> dict[str, Any]:
    with directory(workspace, ["outputs", "desktop"], create=True) as target_dir:
        temporary = f".desktop.kai-upload-{uuid4().hex}"
        output = None
        staging = None
        try:
            os.mkdir(temporary, 0o700, dir_fd=target_dir)
            staging = os.open(
                temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target_dir
            )
            output = os.open(
                "payload",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=staging,
            )
            value = hashlib.sha256()
            total = 0
            while data := os.read(source, CHUNK):
                check()
                total += len(data)
                if total > before.st_size:
                    raise ValueError("desktop export source changed during copy")
                value.update(data)
                pending = memoryview(data)
                while pending:
                    written = os.write(output, pending)
                    if written <= 0:
                        raise OSError("desktop export write made no progress")
                    pending = pending[written:]
            check()
            if total != before.st_size or signature(os.fstat(source)) != signature(before):
                raise ValueError("desktop export source changed during copy")
            os.fsync(output)
            os.fchmod(output, 0o600)
            try:
                # Atomic create, never replace a different existing user file.
                os.link(
                    "payload",
                    name,
                    src_dir_fd=staging,
                    dst_dir_fd=target_dir,
                    follow_symlinks=False,
                )
            except FileExistsError:
                existing = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=target_dir
                )
                try:
                    info = regular(existing, uid)
                    if info.st_size != total or digest(existing, check) != value.hexdigest():
                        raise FileExistsError("export name is already used; choose another name")
                    if signature(os.fstat(existing)) != signature(info):
                        raise ValueError("existing export changed during verification")
                finally:
                    os.close(existing)
            return {
                "ok": True,
                "path": f"outputs/desktop/{name}",
                "size_bytes": total,
                "sha256": value.hexdigest(),
            }
        finally:
            if output is not None:
                os.close(output)
                os.unlink("payload", dir_fd=staging)
            if staging is not None:
                os.close(staging)
                with contextlib.suppress(FileNotFoundError, OSError):
                    os.rmdir(temporary, dir_fd=target_dir)


def run(request: dict[str, Any], *, state_root: Path) -> dict[str, Any]:
    if request.keys() != {"scope", "deadline", "payload", "desktop_epoch"}:
        raise ValueError("desktop export request shape is invalid")
    scope = request["scope"]
    deadline = request["deadline"]
    if (
        not isinstance(scope, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", scope) is None
        or type(deadline) not in {int, float}
        or not time.time() <= deadline <= time.time() + 15
    ):
        raise ValueError("desktop export scope or deadline is invalid")

    def check() -> None:
        state = json.loads((state_root / "state.json").read_bytes())
        if (
            time.time() > deadline
            or not state.get("active")
            or state.get("lease_id") != scope
            or state.get("expires_at", 0) <= time.time()
        ):
            raise PermissionError("desktop export lost its execution lease")

    return export_result(request["payload"], check)


def export_result(
    payload: dict[str, Any], check: Callable[[], None], *, workspace: Path | None = None,
) -> dict[str, Any]:
    try:
        return copy_file(payload, check, workspace=workspace)
    except FileNotFoundError:
        return {
            "ok": False,
            "error": {
                "code": "FILE_NOT_FOUND",
                "message": (
                    "请先将文件保存到 Bot 工作目录，再用相对路径导出" if workspace is not None
                    else "请先将已完成的文件保存到桌面 ~/deliver，再用相对文件名导出"
                ),
            },
        }
    except FileExistsError:
        return {
            "ok": False,
            "error": {
                "code": "NAME_EXISTS",
                "message": "任务工作区已有不同的同名文件，请用 name 指定新文件名",
            },
        }
    except ValueError as error:
        return {"ok": False, "error": {"code": "INVALID_EXPORT", "message": str(error)}}


if __name__ == "__main__":
    # The Bot exporter inherits the active execution namespace and slot UID.
    if os.geteuid() == 0:
        raise PermissionError("Bot workspace export requires its unprivileged account")
    print(json.dumps(export_result(json.load(sys.stdin), lambda: None, workspace=Path.cwd())))
