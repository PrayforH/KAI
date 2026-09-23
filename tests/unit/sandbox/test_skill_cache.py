import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.sandbox.skill_cache import stage_cached_skill_files


class LocalSandbox:
    """Exercise the actual remote script and transfer contract without a provider."""

    def __init__(self):
        self.sandbox_id = "test-sandbox"
        self.commands = self
        self.files = self
        self.bytes = 0
        self.writes = 0

    async def run(self, command, **kwargs):
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return SimpleNamespace(
            exit_code=process.returncode, stdout=stdout.decode(), stderr=stderr.decode()
        )

    async def write_files(self, entries, **kwargs):
        for entry in entries:
            path = Path(entry["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(entry["data"])
            self.bytes += len(entry["data"])
            self.writes += 1


@pytest.mark.asyncio
async def test_warm_run_skips_unchanged_bytes_and_repairs_poisoned_cache(tmp_path):
    sandbox = LocalSandbox()
    name = ".claude/skills/report/SKILL.md"
    data = b"Original instructions"
    a, b, c = [tmp_path / x for x in ("run-a", "run-b", "run-c")]
    await stage_cached_skill_files(sandbox, [(str(a / name), data)], str(a))
    first_bytes = sandbox.bytes
    assert (a / name).read_bytes() == data
    (a / name).write_bytes(b"user edited workspace")
    await stage_cached_skill_files(sandbox, [(str(b / name), data)], str(b))
    assert sandbox.bytes == first_bytes
    assert (b / name).read_bytes() == data
    cached = tmp_path / ".skill-cache-v1" / hashlib.sha256(data).hexdigest()
    cached.write_bytes(b"poisoned")
    await stage_cached_skill_files(sandbox, [(str(c / name), data)], str(c))
    assert sandbox.bytes == 2 * first_bytes
    assert (c / name).read_bytes() == cached.read_bytes() == data
    new_data = b"New instructions"
    await stage_cached_skill_files(sandbox, [(str(c / name), new_data)], str(c))
    assert sandbox.bytes == 2 * first_bytes + len(new_data)
    assert (c / name).read_bytes() == new_data


@pytest.mark.asyncio
async def test_duplicate_content_batching_and_inputs_exclusion(tmp_path):
    sandbox = LocalSandbox()
    workspace = tmp_path / "run"
    entries = [(str(workspace / f".claude/skills/a/file-{i}.txt"), b"same") for i in range(150)]
    await stage_cached_skill_files(sandbox, entries, str(workspace))
    assert sandbox.writes == 1
    assert len(list(workspace.rglob("*.txt"))) == 150
    with pytest.raises(ValueError, match="Only Skill"):
        await stage_cached_skill_files(
            sandbox, [(str(workspace / "inputs/a.txt"), b"private")], str(workspace)
        )
    assert not (workspace / "inputs/a.txt").exists()
