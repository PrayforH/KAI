"""Large transfers must survive a body-limited gateway without partial replacements."""

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from harness.sandbox.e2b import SdkE2BRemoteSandbox


class BodyLimitedFiles:
    def __init__(self, limit: int, *, fail_after: int | None = None) -> None:
        self.limit = limit
        self.fail_after = fail_after
        self.body_sizes: list[int] = []
        self.staged: list[Path] = []

    async def make_dir(self, path: str) -> None:
        Path(path).mkdir(parents=True)
        self.staged.append(Path(path))

    async def remove(self, path: str) -> None:
        assert Path(path) in self.staged
        shutil.rmtree(path)

    async def write(self, path: str, content: bytes) -> None:
        await self.write_files([{"path": path, "data": content}], request_timeout=30)

    async def write_files(
        self, files: list[dict[str, Any]], *, request_timeout: float
    ) -> None:
        assert request_timeout > 0
        size = sum(len(entry["data"]) for entry in files)
        assert size <= self.limit, "413 Request Entity Too Large"
        if self.fail_after is not None and len(self.body_sizes) >= self.fail_after:
            raise RuntimeError("transfer interrupted")
        self.body_sizes.append(size)
        for entry in files:
            path = Path(entry["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(entry["data"])


class LocalCommands:
    async def run(self, command: str, *, timeout: float) -> SimpleNamespace:
        process = await asyncio.create_subprocess_exec("sh", "-c", command)
        return SimpleNamespace(exit_code=await asyncio.wait_for(process.wait(), timeout))


def remote(files: BodyLimitedFiles) -> SdkE2BRemoteSandbox:
    return SdkE2BRemoteSandbox(cast(Any, SimpleNamespace(
        sandbox_id="large-upload-test", files=files, commands=LocalCommands(),
    )))


@pytest.mark.asyncio
async def test_large_file_and_mixed_batches_stay_bounded_and_preserve_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("harness.sandbox.e2b._UPLOAD_BATCH_BYTES", 4)
    files = BodyLimitedFiles(4)
    target = tmp_path / "中文 ' file.rar"
    content = b"\0\xff0123456789"
    await remote(files).upload_many([
        (str(tmp_path / "first.txt"), b"ab"),
        (str(target), content),
        (str(tmp_path / "last.txt"), b"end"),
    ])
    assert target.read_bytes() == content
    assert (tmp_path / "first.txt").read_bytes() == b"ab"
    assert (tmp_path / "last.txt").read_bytes() == b"end"
    assert files.body_sizes == [2, 4, 4, 4, 3]
    assert all(not path.exists() for path in files.staged)


@pytest.mark.asyncio
async def test_interrupted_large_upload_retains_old_destination_and_removes_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("harness.sandbox.e2b._UPLOAD_BATCH_BYTES", 4)
    target = tmp_path / "existing.bin"
    target.write_bytes(b"original")
    files = BodyLimitedFiles(4, fail_after=1)
    with pytest.raises(RuntimeError, match="transfer interrupted"):
        await remote(files).upload(str(target), b"0123456789")
    assert target.read_bytes() == b"original"
    assert all(not path.exists() for path in files.staged)
