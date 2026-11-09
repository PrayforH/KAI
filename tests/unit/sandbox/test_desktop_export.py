"""Desktop publication stays inside its outbox and reuses workspace file bytes."""

import hashlib
import json
import os
import time

import pytest

from harness.computer import desktop_export as export


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    source, workspace = tmp_path / "deliver", tmp_path / "workspace"
    source.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(export, "OUTBOX", source)
    monkeypatch.setattr(export, "WORKSPACE", workspace)
    monkeypatch.setattr(export, "DESKTOP_UID", os.getuid())
    monkeypatch.setattr(export, "TASK_UID", os.getuid())
    return source, workspace


@pytest.mark.parametrize(
    "name,data",
    [
        ("文本.txt", "桌面产物\n".encode()),
        ("clip.mp4", b"\0\x01\xff\x80video\0"),
        ("empty.txt", b""),
    ],
)
def test_all_file_types_share_a_byte_preserving_idempotent_export(bridge, name, data):
    source, workspace = bridge
    (source / name).write_bytes(data)
    first = export.copy_file({"path": name}, lambda: None)
    assert first == {
        "ok": True,
        "path": f"outputs/desktop/{name}",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert (workspace / first["path"]).read_bytes() == data
    assert export.copy_file({"path": name}, lambda: None) == first
    assert sorted(p.name for p in (workspace / "outputs/desktop").iterdir()) == [name]
    assert (source / name).read_bytes() == data


@pytest.mark.parametrize(
    "payload",
    [
        {"path": "/tmp/clip.mp4"},
        {"path": "../secret"},
        {"path": "a/../secret"},
        {"path": "./secret"},
        {"path": "a//secret"},
        {"path": "a\0b"},
        {"path": "f", "name": "../other"},
        {"path": "f", "name": ""},
        {"path": "f", "name": "."},
        {"path": "f", "command": "anything"},
    ],
)
def test_closed_path_protocol_refuses_arbitrary_sources_or_destinations(payload):
    with pytest.raises(ValueError):
        export.arguments(payload)


def test_nested_file_and_explicit_name(bridge):
    source, workspace = bridge
    (source / "folder").mkdir()
    (source / "folder/a.txt").write_text("hello")
    result = export.copy_file({"path": "folder/a.txt", "name": "report.txt"}, lambda: None)
    assert (workspace / result["path"]).read_text() == "hello"


def test_never_overwrites_a_different_existing_file(bridge):
    source, workspace = bridge
    (source / "a.txt").write_text("one")
    export.copy_file({"path": "a.txt"}, lambda: None)
    (source / "a.txt").write_text("two")
    with pytest.raises(FileExistsError):
        export.copy_file({"path": "a.txt"}, lambda: None)
    assert (workspace / "outputs/desktop/a.txt").read_text() == "one"
    assert [p.name for p in (workspace / "outputs/desktop").iterdir()] == ["a.txt"]


@pytest.mark.parametrize(
    "kind", ["source_file", "source_parent", "workspace_parent", "destination"]
)
def test_symlinks_are_not_followed_in_either_direction(bridge, tmp_path, kind):
    source, workspace = bridge
    secret = tmp_path / "private"
    secret.mkdir()
    (secret / "a").write_bytes(b"private")
    payload = {"path": "a"}
    if kind == "source_file":
        (source / "a").symlink_to(secret / "a")
    elif kind == "source_parent":
        (source / "nested").symlink_to(secret)
        payload = {"path": "nested/a"}
    else:
        (source / "a").write_bytes(b"export")
        if kind == "workspace_parent":
            (workspace / "outputs").symlink_to(secret)
        else:
            (workspace / "outputs/desktop").mkdir(parents=True)
            (workspace / "outputs/desktop/a").symlink_to(secret / "a")
    with pytest.raises((OSError, ValueError)):
        export.copy_file(payload, lambda: None)
    assert (secret / "a").read_bytes() == b"private"
    assert sorted(p.name for p in secret.iterdir()) == ["a"]


def test_hardlinks_and_unowned_files_are_refused(bridge, monkeypatch):
    source, _ = bridge
    (source / "a").write_bytes(b"private")
    os.link(source / "a", source / "b")
    with pytest.raises(ValueError, match="without links"):
        export.copy_file({"path": "a"}, lambda: None)
    (source / "b").unlink()
    monkeypatch.setattr(export, "DESKTOP_UID", os.getuid() + 1)
    with pytest.raises(ValueError, match="owned regular"):
        export.copy_file({"path": "a"}, lambda: None)


def test_limit_is_checked_before_copy(bridge, monkeypatch):
    source, workspace = bridge
    (source / "a").write_bytes(b"oversized")
    monkeypatch.setattr(export, "MAX_BYTES", 4)
    with pytest.raises(ValueError, match="exceeds"):
        export.copy_file({"path": "a"}, lambda: None)
    assert not (workspace / "outputs").exists()


@pytest.mark.parametrize("failure", ["lease", "mutation"])
def test_interrupted_or_changing_file_is_not_published(bridge, failure):
    source, workspace = bridge
    (source / "a").write_bytes(b"original")
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 3:
            if failure == "lease":
                raise PermissionError("lease lost")
            (source / "a").write_bytes(b"rewritten")

    with pytest.raises((PermissionError, ValueError)):
        export.copy_file({"path": "a"}, check)
    assert not list((workspace / "outputs/desktop").iterdir())


def test_expired_or_wrong_execution_cannot_read_outbox(bridge, tmp_path):
    source, _ = bridge
    (source / "a").write_bytes(b"private")
    (tmp_path / "state.json").write_text(
        json.dumps({"active": True, "lease_id": "other", "expires_at": time.time() + 30})
    )
    request = {
        "scope": "current",
        "deadline": time.time() + 10,
        "desktop_epoch": 1,
        "payload": {"path": "a"},
    }
    with pytest.raises(PermissionError, match="execution lease"):
        export.run(request, state_root=tmp_path)
    request["deadline"] = time.time() - 1
    with pytest.raises(ValueError, match="deadline"):
        export.run(request, state_root=tmp_path)


def test_workdisk_writer_drops_identity_and_restores_it_even_on_lease_loss(monkeypatch):
    state = {"uid": 0, "gid": 0, "groups": [0, 12]}
    monkeypatch.setattr(export.os, "geteuid", lambda: state["uid"])
    monkeypatch.setattr(export.os, "getegid", lambda: state["gid"])
    monkeypatch.setattr(export.os, "getgroups", lambda: state["groups"])
    monkeypatch.setattr(export.os, "seteuid", lambda value: state.update(uid=value))
    monkeypatch.setattr(export.os, "setegid", lambda value: state.update(gid=value))
    monkeypatch.setattr(export.os, "setgroups", lambda value: state.update(groups=value))

    def check():
        assert state["uid"] == 0
        raise PermissionError("lease lost")

    with pytest.raises(PermissionError, match="lease lost"), export.task_writer(check) as guarded:
        assert state == {"uid": export.TASK_UID, "gid": export.TASK_UID, "groups": []}
        guarded()
    assert state == {"uid": 0, "gid": 0, "groups": [0, 12]}
def test_bot_exports_same_filename_to_its_own_workspace(tmp_path):
    for bot, content in (("a", b"Bot A"), ("b", b"Bot B")):
        workspace = tmp_path / bot
        workspace.mkdir()
        (workspace / "report.txt").write_bytes(content)
        result = export.export_result({"path": "report.txt"}, lambda: None, workspace=workspace)
        assert result["path"] == "outputs/desktop/report.txt"
        assert (workspace / result["path"]).read_bytes() == content
        assert export.export_result(
            {"path": "../a/report.txt"}, lambda: None, workspace=workspace,
        )["error"]["code"] == "INVALID_EXPORT"
