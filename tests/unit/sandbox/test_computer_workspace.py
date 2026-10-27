import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from harness.computer.docker import DockerComputerDriver
from harness.computer.manager import ComputerAllocation
from harness.computer.workspace import ComputerWorkspace
from harness.computer.workspace_guest import CHUNK_BYTES, Workspace, parts
from harness.sandbox.base import SandboxCommandResult


class UserFilePlane:
    """Execute the exact guest functions on real files, without a mock filesystem."""

    def __init__(self, root: Path):
        self.guest = Workspace(root)
        self.calls: list[str] = []

    async def execute(
        self, allocation: ComputerAllocation, argv: list[str], *, stdin: bytes, timeout_seconds: int
    ) -> SandboxCommandResult:
        assert argv[:3] == ["python3", "-I", "-c"]
        self.calls.append(argv[-1])
        try:
            result = self.guest.invoke(argv[-1], json.loads(stdin))
            return SandboxCommandResult(exit_code=0, stdout=json.dumps(result))
        except (OSError, ValueError, TypeError):
            return SandboxCommandResult(exit_code=1)


def plane(root: Path) -> tuple[UserFilePlane, ComputerWorkspace]:
    host = UserFilePlane(root)
    workspace = ComputerWorkspace(
        cast(DockerComputerDriver, host),
        cast(ComputerAllocation, object()),
        max_bytes=CHUNK_BYTES * 4,
        max_members=100,
    )
    return host, workspace


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../secret", "a/../b", "a//b", ".", "a/./b", "a\x00b"]
)
def test_guest_rejects_paths_outside_relative_contract(path: str):
    with pytest.raises(ValueError):
        parts(path)


@pytest.mark.asyncio
async def test_chunked_transfer_is_atomic_and_preserves_executable_mode(tmp_path: Path):
    root, local = tmp_path / "remote", tmp_path / "local"
    root.mkdir()
    local.mkdir()
    host, workspace = plane(root)
    content = b"tool-data" * (CHUNK_BYTES // 8 + 1)
    await workspace.capture_baseline()
    await workspace.upload([("tools/fixture", content, 0o700), ("empty", b"", 0o600)])
    assert host.calls.count("upload") == 3
    assert await workspace.download("tools/fixture", max_bytes=len(content)) == content
    assert await workspace.download("empty", max_bytes=0) == b""
    await workspace.collect(local)
    assert (local / "tools/fixture").read_bytes() == content
    assert (local / "tools/fixture").stat().st_mode & 0o777 == 0o700
    assert not list(root.rglob("*.kai-upload-*"))


@pytest.mark.asyncio
async def test_old_outputs_are_not_collected_or_uploaded_as_new_turn(tmp_path: Path):
    root, local = tmp_path / "remote", tmp_path / "fresh-run"
    (root / "outputs").mkdir(parents=True)
    local.mkdir()
    (root / "outputs/old.txt").write_text("previous result")
    (root / "project.py").write_text("retained project")
    (local / "inputs").mkdir()
    (local / "inputs/new.txt").write_text("this turn")
    (local / ".harness-runtime").mkdir()
    (local / ".harness-runtime/private-token").write_text("fixture-control-token")
    _, workspace = plane(root)
    await workspace.capture_baseline()
    await workspace.stage(local)
    assert not (root / ".harness-runtime").exists()
    assert (root / "project.py").read_text() == "retained project"
    assert (root / "inputs/new.txt").read_text() == "this turn"
    await workspace.collect(local)
    assert not (local / "outputs/old.txt").exists()
    assert not (local / "project.py").exists()
    (root / "outputs/new.txt").write_text("new result")
    (root / "project.py").write_text("changed project")
    await workspace.collect(local)
    assert (local / "outputs/new.txt").read_text() == "new result"
    assert (local / "project.py").read_text() == "changed project"
    assert (
        workspace.fingerprints()["outputs/old.txt"]
        == hashlib.sha256(b"previous result").hexdigest()
    )
    (root / "outputs/new.txt").unlink()
    (root / "project.py").write_text("retained project")
    await workspace.collect(local)
    assert not (local / "outputs/new.txt").exists()
    assert not (local / "project.py").exists()
    assert (local / "inputs/new.txt").is_file()


@pytest.mark.asyncio
async def test_rejects_symlink_parents_and_does_not_collect_symlinks(tmp_path: Path):
    root, outside = tmp_path / "remote", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret").write_bytes(b"private")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "file-link").symlink_to(outside / "secret")
    _, workspace = plane(root)
    await workspace.capture_baseline()
    assert workspace.fingerprints() == {}
    for path in ["escape/secret", "file-link"]:
        with pytest.raises(RuntimeError):
            await workspace.download(path, max_bytes=100)
    with pytest.raises(RuntimeError):
        await workspace.upload([("escape/secret", b"overwritten", 0o600)])
    assert (outside / "secret").read_bytes() == b"private"


def test_corrupt_upload_never_replaces_original_and_can_be_aborted(tmp_path: Path):
    target = tmp_path / "report"
    target.write_bytes(b"original")
    guest = Workspace(tmp_path)
    payload: dict[str, Any] = {
        "path": "report",
        "upload_id": "f" * 32,
        "offset": 0,
        "size": 3,
        "mode": 0o600,
        "sha256": "0" * 64,
        "data": base64.b64encode(b"bad").decode(),
    }
    with pytest.raises(ValueError, match="checksum"):
        guest.upload(payload)
    assert target.read_bytes() == b"original"
    guest.abort("report", "f" * 32)
    assert list(tmp_path.iterdir()) == [target]


def test_file_changed_between_chunks_is_rejected(tmp_path: Path):
    target = tmp_path / "report"
    target.write_bytes(b"original")
    guest = Workspace(tmp_path)
    info = guest.describe_file("report", 100)
    target.write_bytes(b"modified")
    with pytest.raises(ValueError, match="changed"):
        guest.read("report", 0, 8, info["signature"])


def test_fifo_is_refused_without_blocking_and_dependencies_are_not_scanned(tmp_path: Path):
    guest = Workspace(tmp_path)
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ValueError, match="regular"):
        guest.describe_file("pipe", 100)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/large").write_bytes(b"x" * 1000)
    assert guest.manifest(10, 10) == {}


@pytest.mark.asyncio
async def test_collection_refuses_local_mirror_symlink_escape(tmp_path: Path):
    root, local, outside = (tmp_path / n for n in ["remote", "local", "outside"])
    root.mkdir()
    local.mkdir()
    outside.mkdir()
    _, workspace = plane(root)
    await workspace.capture_baseline()
    (root / "outputs").mkdir()
    (root / "outputs/report").write_text("new")
    (local / "outputs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escaped"):
        await workspace.collect(local)
    assert list(outside.iterdir()) == []
