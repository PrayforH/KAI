"""Verified content cache inside a warm sandbox, outside each Run workspace.

Only Skill files are cached. Inputs, outputs and credentials always use the
ordinary workspace transfer. Cached bytes are rehashed before each reuse.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
from collections.abc import Sequence
from typing import Any, cast
from uuid import uuid4

# Executed as trusted inline source, never imported from sandbox-owned files.
_SCRIPT = r"""
import hashlib,json,os,pathlib,shutil,sys
mode,root,workspace,items,staging=json.loads(sys.argv[1])
root=pathlib.Path(root); workspace=pathlib.Path(workspace)
if root.is_symlink(): raise ValueError("Unsafe Skill cache")
root.mkdir(parents=True,exist_ok=True,mode=0o700)
missing=[]
for relative,digest,size in items:
    if len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid Skill digest")
    parts=pathlib.PurePosixPath(relative)
    if parts.is_absolute() or ".." in parts.parts: raise ValueError("Unsafe Skill path")
    cached=root/digest
    def valid(path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=size: return False
        h=hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda:f.read(1048576),b""): h.update(chunk)
        return h.hexdigest()==digest
    if not valid(cached) and mode=="commit":
        candidate=root/(staging+digest)
        if not valid(candidate): raise ValueError("Skill cache upload failed integrity check")
        candidate.replace(cached)
    if not valid(cached):
        missing.append(digest); continue
    if mode=="commit":
        target=workspace/parts
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink() or not target.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("Unsafe Skill destination")
        shutil.copyfile(cached,target)
        os.utime(cached,None)
if mode=="commit":
    entries=[(p.stat().st_mtime,p.stat().st_size,p) for p in root.iterdir()
             if p.is_file() and not p.is_symlink() and not p.name.startswith(".")]
    total=sum(x[1] for x in entries)
    for _,size,path in sorted(entries):
        if total<=268435456: break
        path.unlink(missing_ok=True); total-=size
print(json.dumps(sorted(set(missing))))
"""


def is_skill_path(relative: str) -> bool:
    parts = relative.split("/")
    return len(parts) >= 4 and parts[0] in {".claude", ".agents"} and parts[1] == "skills"


async def stage_cached_skill_files(
    sandbox: Any,
    entries: Sequence[tuple[str, bytes]],
    workspace: str,
) -> None:
    """Use SDK commands/files; a miss uploads only the missing content hashes."""
    cache = posixpath.join(posixpath.dirname(workspace), ".skill-cache-v1")
    # Keep command arguments bounded for large packages.
    for offset in range(0, len(entries), 128):
        batch = entries[offset : offset + 128]
        items = [
            (path.removeprefix(workspace + "/"), hashlib.sha256(data).hexdigest(), len(data))
            for path, data in batch
        ]
        if any(not is_skill_path(item[0]) for item in items):
            raise ValueError("Only Skill files may enter the Skill cache")
        staging = f".tmp-{uuid4().hex}-"

        async def command(
            mode: str,
            items: list[tuple[str, str, int]] = items,
            staging: str = staging,
        ) -> list[str]:
            payload = json.dumps([mode, cache, workspace, items, staging], separators=(",", ":"))
            result = await sandbox.commands.run(
                "python3 -c " + shlex.quote(_SCRIPT) + " " + shlex.quote(payload),
                timeout=60,
            )
            if result.exit_code != 0:
                raise RuntimeError("Sandbox Skill cache preparation failed")
            missing = json.loads(result.stdout)
            if not isinstance(missing, list) or any(
                value not in {item[1] for item in items} for value in cast(list[Any], missing)
            ):
                raise ValueError("Invalid Skill cache response")
            return cast(list[str], missing)

        missing = set(await command("probe"))
        uploads = {
            digest: data
            for (_, data), (_, digest, _) in zip(batch, items, strict=True)
            if digest in missing
        }
        if uploads:
            # Reuse the adapter's bounded multipart writes for large assets.
            from harness.sandbox.e2b import SdkE2BRemoteSandbox

            adapter = SdkE2BRemoteSandbox(sandbox)
            await adapter.upload_many(
                [(f"{cache}/{staging}{digest}", data) for digest, data in uploads.items()]
            )
        if await command("commit"):
            raise RuntimeError("Sandbox Skill cache is incomplete")
