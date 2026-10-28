"""Stdlib file plane, executed by the Computer supervisor as the task user.

Every path is opened relative to held directory descriptors, without following
symlinks. The module is shipped as source, not imported into the remote Python.
"""

import base64
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

CHUNK_BYTES = 2 * 1024**2
BATCH_FILES = 64
EXCLUDED = frozenset(
    {
        ".git",
        ".claude",
        ".harness-runtime",
        ".tmp",
        "inputs",
        ".cache",
        ".npm",
        ".pnpm-store",
        ".venv",
        ".yarn",
        "__pycache__",
        "node_modules",
        "vendor",
        "venv",
    }
)
_TEMPORARY = re.compile(r"^\..+\.kai-upload-[a-f0-9]{32}$")


def parts(path: str) -> list[str]:
    values = path.split("/")
    if len(path) > 4096 or "\x00" in path or any(v in {"", ".", ".."} for v in values):
        raise ValueError("invalid workspace-relative path")
    return values


def signature(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def directory(self, components: list[str], *, create: bool = False) -> Generator[int]:
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in components:
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    @contextmanager
    def file(parent: int, name: str) -> Generator[int]:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("workspace entry is not a regular file")
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def describe(fd: int) -> dict[str, Any]:
        before = os.fstat(fd)
        digest = hashlib.sha256()
        while data := os.read(fd, CHUNK_BYTES):
            digest.update(data)
        if signature(os.fstat(fd)) != signature(before):
            raise ValueError("workspace file changed during inspection")
        return {
            "sha256": digest.hexdigest(),
            "size": before.st_size,
            "mode": before.st_mode & 0o777,
            "signature": signature(before),
        }

    def describe_file(self, path: str, max_bytes: int) -> dict[str, Any]:
        values = parts(path)
        with self.directory(values[:-1]) as directory, self.file(directory, values[-1]) as fd:
            if not 0 <= os.fstat(fd).st_size <= max_bytes:
                raise ValueError("workspace file exceeds read limit")
            return self.describe(fd)

    def manifest(self, max_bytes: int, max_members: int) -> dict[str, Any]:
        if min(max_bytes, max_members) < 1:
            raise ValueError("invalid workspace limits")
        result: dict[str, Any] = {}
        total = visited = 0

        def walk(directory: int, prefix: tuple[str, ...]) -> None:
            nonlocal total, visited
            if len(prefix) > 128:
                raise ValueError("workspace nesting limit exceeded")
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > max_members:
                        raise ValueError("workspace member limit exceeded")
                    if entry.name in EXCLUDED or _TEMPORARY.fullmatch(entry.name):
                        continue
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        with self.directory([*prefix, entry.name]) as child:
                            walk(child, (*prefix, entry.name))
                    elif entry.is_file(follow_symlinks=False):
                        with self.file(directory, entry.name) as fd:
                            total += os.fstat(fd).st_size
                            if total > max_bytes:
                                raise ValueError("workspace byte limit exceeded")
                            result["/".join((*prefix, entry.name))] = self.describe(fd)

        with self.directory([]) as directory:
            walk(directory, ())
        return result

    def read(self, path: str, offset: int, length: int, expected: list[int]) -> dict[str, Any]:
        if offset < 0 or not 0 < length <= CHUNK_BYTES:
            raise ValueError("invalid workspace read bounds")
        values = parts(path)
        with self.directory(values[:-1]) as directory, self.file(directory, values[-1]) as fd:
            if signature(os.fstat(fd)) != expected:
                raise ValueError("workspace file changed before transfer")
            os.lseek(fd, offset, os.SEEK_SET)
            # POSIX allows read() to return less than requested at any time,
            # and this work disk is 9P: one read spans many transport-sized
            # responses. Keep reading until the span or the file is exhausted
            # instead of treating the first short read as the whole answer.
            data = bytearray()
            while len(data) < length:
                block = os.read(fd, length - len(data))
                if not block:
                    break
                data.extend(block)
            if signature(os.fstat(fd)) != expected:
                raise ValueError("workspace file changed during transfer")
            return {"data": base64.b64encode(bytes(data)).decode("ascii")}

    def upload(self, payload: dict[str, Any]) -> dict[str, bool]:
        values = parts(payload["path"])
        identifier = payload["upload_id"]
        digest = payload["sha256"]
        offset, size, mode = payload["offset"], payload["size"], payload["mode"]
        if (
            re.fullmatch(r"[a-f0-9]{32}", identifier) is None
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or not 0 <= offset <= size
            or not 0 <= mode <= 0o777
        ):
            raise ValueError("invalid workspace upload")
        data = base64.b64decode(payload["data"], validate=True)
        if len(data) > CHUNK_BYTES or offset + len(data) > size:
            raise ValueError("workspace upload chunk exceeds limit")
        temporary = f".{values[-1]}.kai-upload-{identifier}"
        with self.directory(values[:-1], create=True) as directory:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if offset == 0:
                flags |= os.O_CREAT | os.O_EXCL
            fd = os.open(temporary, flags, 0o600, dir_fd=directory)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size != offset:
                    raise ValueError("workspace upload offset changed")
                os.lseek(fd, offset, os.SEEK_SET)
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                if offset + len(data) == size:
                    os.lseek(fd, 0, os.SEEK_SET)
                    if self.describe(fd)["sha256"] != digest:
                        raise ValueError("workspace upload checksum mismatch")
                    os.fchmod(fd, mode)
                    os.replace(temporary, values[-1], src_dir_fd=directory, dst_dir_fd=directory)
                    return {"complete": True}
                return {"complete": False}
            finally:
                os.close(fd)

    def abort(self, path: str, upload_id: str) -> dict[str, bool]:
        values = parts(path)
        if re.fullmatch(r"[a-f0-9]{32}", upload_id) is None:
            raise ValueError("invalid workspace upload identity")
        with self.directory(values[:-1]) as directory:
            try:
                os.unlink(f".{values[-1]}.kai-upload-{upload_id}", dir_fd=directory)
            except FileNotFoundError:
                pass
        return {"aborted": True}

    def upload_batch(self, raw: object) -> dict[str, Any]:
        """Bounded transport batch; each file keeps its own atomic replacement.

        A failed batch is not a multi-file transaction. Earlier completed files
        stay committed, as with sequential uploads; no model tool starts until
        the complete staging operation has succeeded.
        """
        if not isinstance(raw, list):
            raise ValueError("invalid workspace batch size")
        items = cast(list[object], raw)
        if not 1 <= len(items) <= BATCH_FILES:
            raise ValueError("invalid workspace batch size")
        entries: list[dict[str, Any]] = []
        total = 0
        for value in items:
            if not isinstance(value, dict):
                raise ValueError("invalid workspace batch entry")
            entry = cast(dict[str, Any], value)
            if (
                entry.get("offset") != 0
                or not isinstance(entry.get("size"), int)
                or isinstance(entry["size"], bool)
                or not 0 <= entry["size"] <= CHUNK_BYTES
            ):
                raise ValueError("invalid workspace batch entry")
            total += entry["size"]
            if total > CHUNK_BYTES:
                raise ValueError("workspace batch exceeds byte limit")
            entries.append(entry)
        try:
            for entry in entries:
                if not self.upload(entry)["complete"]:
                    raise ValueError("incomplete workspace batch entry")
        finally:
            for entry in entries:
                try:
                    self.abort(entry["path"], entry["upload_id"])
                except (OSError, ValueError, KeyError, TypeError):
                    # The lease watchdog may interrupt an upload, and a later
                    # entry's directory may not exist. Never leave cleanup as
                    # a prerequisite for reporting the original failure.
                    pass
        return {"complete": True, "count": len(entries)}

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "manifest":
            return self.manifest(payload["max_bytes"], payload["max_members"])
        if operation == "stat":
            return self.describe_file(payload["path"], payload["max_bytes"])
        if operation == "read":
            return self.read(
                payload["path"], payload["offset"], payload["length"], payload["signature"]
            )
        if operation == "upload":
            return self.upload(payload)
        if operation == "upload_batch":
            return self.upload_batch(payload["entries"])
        if operation == "abort":
            return self.abort(payload["path"], payload["upload_id"])
        raise ValueError("unknown workspace operation")


def main() -> None:
    try:
        result = Workspace(Path("/home/kai/workspace")).invoke(sys.argv[1], json.load(sys.stdin))
        print(json.dumps(result))
    except (OSError, ValueError, KeyError, TypeError):
        # File names, transferred bytes and exception details may be private.
        print('{"error":"Computer workspace operation could not be confirmed"}')
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
