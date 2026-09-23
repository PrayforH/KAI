import base64
import hashlib
import os

import pytest

from harness.core import skill_file_cache as cache


def test_reuse_copies_and_repairs_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.tempfile, "gettempdir", lambda: str(tmp_path))
    data = b"original\x00bytes"
    digest = hashlib.sha256(data).hexdigest()
    encoded = base64.b64encode(data).decode()
    a, b = tmp_path / "a", tmp_path / "b"
    cache.materialize_file(encoded, digest, len(data), a)
    root = tmp_path / f"harness-skill-files-v1-{os.getuid()}"
    mtime = (root / digest).stat().st_mtime_ns
    cache.materialize_file(encoded, digest, len(data), b)
    assert a.read_bytes() == b.read_bytes() == data
    a.write_bytes(b"changed by runtime")
    assert b.read_bytes() == (root / digest).read_bytes() == data
    assert (root / digest).stat().st_mtime_ns >= mtime
    (root / digest).write_bytes(b"corrupt")
    cache.materialize_file(encoded, digest, len(data), b)
    assert b.read_bytes() == (root / digest).read_bytes() == data
    with pytest.raises(ValueError):
        cache.materialize_file(encoded, "a" * 64, len(data), b)


def test_cache_bound_and_symlink_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(cache, "_LIMIT", 5)
    for data in (b"12345", b"67890"):
        cache.materialize_file(
            base64.b64encode(data).decode(),
            hashlib.sha256(data).hexdigest(),
            len(data),
            tmp_path / "target",
        )
    root = tmp_path / f"harness-skill-files-v1-{os.getuid()}"
    assert sum(p.stat().st_size for p in root.iterdir()) <= 5
    import shutil

    shutil.rmtree(root)
    other = tmp_path / "other"
    other.mkdir()
    root.symlink_to(other, target_is_directory=True)
    data = b"safe"
    cache.materialize_file(
        base64.b64encode(data).decode(),
        hashlib.sha256(data).hexdigest(),
        len(data),
        tmp_path / "target",
    )
    assert (tmp_path / "target").read_bytes() == data
    assert list(other.iterdir()) == []
