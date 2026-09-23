"""Bounded, verified file cache; workspace copies are never hard links."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
from pathlib import Path

_LIMIT = 256 * 1024 * 1024


def materialize_file(encoded: str, digest: str, size: int, target: Path) -> None:
    root = Path(tempfile.gettempdir()) / f"harness-skill-files-v1-{os.getuid()}"
    cached = root / digest
    try:
        if root.is_symlink():
            raise OSError("Skill cache root is a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not cached.is_symlink() and cached.is_file():
            content = cached.read_bytes()
            if len(content) == size and hashlib.sha256(content).hexdigest() == digest:
                target.write_bytes(content)
                os.utime(cached, None)
                return
    except OSError:
        pass  # Cache availability never changes a valid package's behavior.
    content = base64.b64decode(encoded, validate=True)
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("corrupt Skill snapshot file")
    target.write_bytes(content)
    try:
        if root.is_symlink():
            return
        with tempfile.NamedTemporaryFile(dir=root, prefix=".tmp-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        try:
            temporary.replace(cached)
        finally:
            temporary.unlink(missing_ok=True)
        # Bound disk use across many packages and revisions, using access age.
        entries = [
            (p.stat().st_mtime, p.stat().st_size, p)
            for p in root.iterdir()
            if p.is_file() and not p.is_symlink() and not p.name.startswith(".")
        ]
        total = sum(entry[1] for entry in entries)
        for _, length, path in sorted(entries):
            if total <= _LIMIT:
                break
            path.unlink(missing_ok=True)
            total -= length
    except OSError:
        pass
