"""Durable per-incarnation identity on the node, outside user VM mounts."""

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import cast

from harness.computer.vm_seed import ComputerVmSeed, create_vm_seed

_FILES = ("control", "viewer", "user-data", "meta-data", "network-config", "known_hosts")


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("computer key directory is not private")


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > 65536
        ):
            raise PermissionError("computer key file is not private")
        return os.read(fd, 65537)
    finally:
        os.close(fd)


def _write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class VmSeedStore:
    """A failed partial creation is never silently replaced with new keys.

    The lifecycle controller supplies both identities after canonical lookup.
    Callers must not select directories or incarnations from a user payload.
    Parent directories are administrator-controlled; only this private root
    and its children are used here. Nothing in this root goes in a VM mount.
    """

    def __init__(self, root: Path) -> None:
        _private_directory(root)
        self.root = root

    def prepare(self, computer_id: str, incarnation: str) -> ComputerVmSeed:
        if (
            re.fullmatch(r"computer_[a-f0-9]{32}", computer_id) is None
            or re.fullmatch(r"[a-f0-9]{32}", incarnation) is None
        ):
            raise ValueError("invalid computer VM identity")
        _private_directory(self.root)
        path = self.root / f"{computer_id}-{incarnation}"
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            return self._load(path, computer_id, incarnation)
        seed = create_vm_seed(computer_id, incarnation)
        files = dict(
            zip(
                _FILES,
                (
                    seed.control_private_key,
                    seed.viewer_private_key,
                    seed.user_data,
                    seed.meta_data,
                    seed.network_data,
                    seed.known_hosts(),
                ),
                strict=True,
            )
        )
        for name, data in files.items():
            _write(path / name, data)
        manifest = {
            "version": 2,
            "computer_id": computer_id,
            "instance_id": seed.instance_id,
            "host_public_key": seed.host_public_key,
            "control_public_key": seed.control_public_key,
            "viewer_public_key": seed.viewer_public_key,
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        }
        # Written last. Absence means recovery, not permission to regenerate.
        _write(path / "manifest.json", json.dumps(manifest).encode("ascii"))
        for directory in (path, self.root):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return seed

    @staticmethod
    def _load(path: Path, computer_id: str, incarnation: str) -> ComputerVmSeed:
        _private_directory(path)
        try:
            raw: object = json.loads(_read(path / "manifest.json"))
            if not isinstance(raw, dict):
                raise ValueError
            value = cast(dict[str, object], raw)
            if (
                type(value.get("version")) is not int
                or value.get("version") != 2
                or value.get("computer_id") != computer_id
                or value.get("instance_id") != f"{computer_id}-{incarnation}"
                or not isinstance(value.get("host_public_key"), str)
                or not isinstance(value.get("control_public_key"), str)
                or not isinstance(value.get("viewer_public_key"), str)
            ):
                raise ValueError
            files = {name: _read(path / name) for name in _FILES}
            hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
            if value.get("sha256") != hashes:
                raise ValueError
            seed = ComputerVmSeed(
                computer_id=computer_id,
                instance_id=f"{computer_id}-{incarnation}",
                host_public_key=cast(str, value["host_public_key"]),
                control_public_key=cast(str, value["control_public_key"]),
                viewer_public_key=cast(str, value["viewer_public_key"]),
                control_private_key=files["control"],
                viewer_private_key=files["viewer"],
                user_data=files["user-data"],
                meta_data=files["meta-data"],
                network_data=files["network-config"],
            )
            if seed.known_hosts() != files["known_hosts"]:
                raise ValueError
            return seed
        except (FileNotFoundError, ValueError, KeyError):
            raise RuntimeError(
                "computer identity is incomplete or damaged; recovery required"
            ) from None
