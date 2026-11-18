"""Bot-controlled profile paths cannot redirect privileged setup."""

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.computer import computer_control
from harness.computer.computer_control import _open_directory


def test_slot_setup_refuses_profile_symlinks(tmp_path: Path) -> None:
    private = tmp_path / "outside"
    private.mkdir(mode=0o755)
    profile = tmp_path / "chromium"
    profile.symlink_to(private, target_is_directory=True)

    with pytest.raises(OSError):
        _open_directory(profile, (os.getuid(), os.getgid()))

    assert private.stat().st_mode & 0o777 == 0o755


def test_preparing_a_shell_slot_does_not_authorize_desktop_start(tmp_path, monkeypatch):
    desktop_id = "desktop_" + "a" * 24
    slot = SimpleNamespace(desktop_id=desktop_id)
    requests = tmp_path / "requests"
    actions = []
    (tmp_path / f"{desktop_id}.env").touch()
    monkeypatch.setattr(computer_control, "REQUESTS", requests)
    monkeypatch.setattr(computer_control, "SESSIONS", tmp_path)
    monkeypatch.setattr(computer_control, "_prepare", lambda _slot: actions.append("prepare"))
    monkeypatch.setattr(computer_control, "_systemctl", lambda verb, _unit: actions.append(verb))
    monkeypatch.setattr(computer_control, "_wait_for_display", lambda _slot: "active")

    assert computer_control.apply("prepare", slot) == "prepared"
    assert not requests.exists()
    assert actions == ["prepare"]

    assert computer_control.apply("start", slot) == "active"
    assert (requests / desktop_id).is_file()
    assert actions == ["prepare", "start"]


@pytest.mark.parametrize(
    ("mode", "gid", "taken"),
    [(0o700, 28111, False), (0o755, 28111, False), (0o700, 28112, False), (0o700, 28111, True)],
)
def test_rebuilt_slot_restores_only_its_available_private_uid(monkeypatch, mode, gid, taken):
    stored_home = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=28111, st_gid=gid)
    monkeypatch.setattr(Path, "lstat", lambda self: stored_home)
    monkeypatch.setattr(computer_control, "_taken", lambda uid: taken)
    slot = SimpleNamespace(home="/home/kai-bots/test")
    if mode == 0o700 and gid == 28111 and not taken:
        assert computer_control._uid_for_new_account(slot) == 28111
    else:
        with pytest.raises(PermissionError):
            computer_control._uid_for_new_account(slot)
