"""Configured Bot data is opened only from its enrolled 9P workdisk."""

import pytest

from harness.computer import bot_workdisk as disk


def test_node_expected_identity_disallows_unconfigured_guest(monkeypatch):
    monkeypatch.setattr(disk, "_identity", lambda: None)
    disk.require()
    with pytest.raises(PermissionError, match="node"):
        disk.require(expected="computer")


@pytest.mark.parametrize(
    ("root", "options", "filesystem"),
    [
        ("/", "rw,nosuid,nodev", ["ext4", "/dev/vda1"]),
        ("/", "rw,nosuid,nodev", ["9p", "kai_bots"]),
        ("/data", "rw,nosuid,nodev", ["9p", "other"]),
        ("/data", "rw,nosuid", ["9p", "kai_bots"]),
    ],
)
def test_bound_workdisk_must_be_the_expected_safe_subtree(monkeypatch, root, options, filesystem):
    monkeypatch.setattr(disk, "_identity", lambda: "computer")
    monkeypatch.setattr(disk, "_mounted", lambda path: (
        ["50", "20", "0:20", root, str(path), options], filesystem,
    ))
    with pytest.raises(PermissionError, match="not mounted"):
        disk.require(expected="computer")


def test_other_guest_marker_is_refused(tmp_path, monkeypatch):
    (tmp_path / disk.MARKER).write_text("other\n")
    monkeypatch.setattr(disk, "_trusted", lambda path, **kwargs: None)
    with pytest.raises(PermissionError, match="does not match the guest"):
        disk._check_marker(tmp_path, "computer")


def test_normal_mount_does_not_import_an_uninitialized_export(tmp_path, monkeypatch):
    bots = tmp_path / "kai-bots"
    export = tmp_path / "export"
    bots.mkdir()
    export.mkdir()
    (bots / "old-file").write_text("keep on the system disk")
    monkeypatch.setattr(disk, "BOTS", bots)
    monkeypatch.setattr(disk, "EXPORT", export)
    monkeypatch.setattr(disk, "_identity", lambda: "computer")
    monkeypatch.setattr(disk, "_trusted", lambda path, **kwargs: path.lstat())

    def mounted(path):
        if path == bots:
            return ["40", "20", "0:20", "/", "/", "rw"], ["ext4", "/dev/vda1"]
        return ["50", "20", "0:21", "/", str(export), "rw,nosuid,nodev"], ["9p", "kai_bots"]

    monkeypatch.setattr(disk, "_mounted", mounted)
    monkeypatch.setattr(
        disk.subprocess, "run", lambda *args, **kwargs: pytest.fail("unexpected mount or copy")
    )
    with pytest.raises(FileNotFoundError):
        disk.mount()
    assert list(export.iterdir()) == []
    assert (bots / "old-file").read_text() == "keep on the system disk"
