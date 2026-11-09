"""Bot-controlled profile paths cannot redirect privileged setup."""

import os
from pathlib import Path

import pytest

from harness.computer.computer_control import _open_directory


def test_slot_setup_refuses_profile_symlinks(tmp_path: Path) -> None:
    private = tmp_path / "outside"
    private.mkdir(mode=0o755)
    profile = tmp_path / "chromium"
    profile.symlink_to(private, target_is_directory=True)

    with pytest.raises(OSError):
        _open_directory(profile, (os.getuid(), os.getgid()))

    assert private.stat().st_mode & 0o777 == 0o755
