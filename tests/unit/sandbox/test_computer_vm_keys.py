"""A retry must retain the exact server pin and client key, even after failure."""

import os
from pathlib import Path

import pytest

from harness.computer.vm_keys import VmSeedStore

COMPUTER = "computer_" + "a" * 32
INCARNATION = "b" * 32


def test_creation_and_retry_keep_identical_credentials(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    store = VmSeedStore(tmp_path)
    first = store.prepare(COMPUTER, INCARNATION)
    second = VmSeedStore(tmp_path).prepare(COMPUTER, INCARNATION)
    assert first == second
    for path in (tmp_path / first.instance_id).iterdir():
        assert path.stat().st_mode & 0o077 == 0
    next_os = store.prepare(COMPUTER, "c" * 32)
    assert first.control_public_key != next_os.control_public_key
    assert first.host_public_key != next_os.host_public_key


def test_interrupted_create_never_regenerates_keys(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / f"{COMPUTER}-{INCARNATION}"
    path.mkdir(mode=0o700)
    partial = path / "control"
    partial.write_bytes(b"partial private fixture")
    partial.chmod(0o600)
    with pytest.raises(RuntimeError, match="recovery required"):
        VmSeedStore(tmp_path).prepare(COMPUTER, INCARNATION)
    assert partial.read_bytes() == b"partial private fixture"
    assert not (path / "manifest.json").exists()


@pytest.mark.parametrize("attack", ["changed-key", "symlink", "hardlink", "public-mode"])
def test_damaged_or_exposed_credentials_are_rejected(tmp_path: Path, attack: str) -> None:
    tmp_path.chmod(0o700)
    store = VmSeedStore(tmp_path)
    seed = store.prepare(COMPUTER, INCARNATION)
    key = tmp_path / seed.instance_id / "control"
    original = key.read_bytes()
    if attack == "changed-key":
        key.write_bytes(b"replacement")
    elif attack == "public-mode":
        key.chmod(0o644)
    else:
        other = tmp_path / "other"
        key.rename(other)
        if attack == "symlink":
            key.symlink_to(other)
        else:
            os.link(other, key)
    with pytest.raises((RuntimeError, PermissionError, OSError)):
        store.prepare(COMPUTER, INCARNATION)
    if attack == "public-mode":
        assert key.read_bytes() == original


def test_store_rejects_public_or_symlink_root(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    with pytest.raises(PermissionError):
        VmSeedStore(tmp_path)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(PermissionError):
        VmSeedStore(link)
