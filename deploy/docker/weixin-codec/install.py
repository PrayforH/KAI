"""Install the same fixed SILK decoder used by Tencent's reference channel."""

import hashlib
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

SHA256 = "668087436d9a7160544eaefd256ce774697fb39c4ff1985897c5bb9c45580df0"
with tempfile.TemporaryDirectory() as directory:
    archive = Path(directory) / "silk-wasm.tgz"
    local = os.getenv("KAI_WEIXIN_SILK_ARCHIVE")
    if local:
        archive.write_bytes(Path(local).read_bytes())
    else:
        registry = os.getenv("NPM_REGISTRY", "https://registry.npmjs.org").rstrip("/")
        urllib.request.urlretrieve(registry + "/silk-wasm/-/silk-wasm-3.7.1.tgz", archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("SILK decoder archive checksum mismatch")
    with tarfile.open(archive) as package:
        package.extractall(directory, filter="data")
    import shutil

    shutil.copytree(Path(directory) / "package", "/opt/kai-weixin-codec", dirs_exist_ok=True)
