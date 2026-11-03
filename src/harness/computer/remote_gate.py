"""Root-owned forced command for a physical Linux computer, not a general shell.

Execution reuses the VM supervisor and its PID/mount namespace fencing. Only
KAI processes are drained; the person's desktop and applications are untouched.
"""

import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

CONFIG = Path("/etc/kai-computer-remote.json")
GATE = Path("/opt/kai-computer/ssh_gate.py")


def configuration() -> dict[str, Any]:
    info = CONFIG.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("remote configuration is not trusted")
    value = json.loads(CONFIG.read_bytes())
    if Path("/etc/machine-id").read_text().strip() != value["machine_id"]:
        raise PermissionError("remote machine identity changed")
    digest = hashlib.sha256()
    components = ["remote_display_gate.py", "remote_gate.py", "ssh_gate.py", "supervisor.py"]
    if value.get("native_desktop"):
        components.append("native_desktop.py")
    for name in components:
        component = Path("/opt/kai-computer") / name
        info = component.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise PermissionError("remote component is not trusted")
        digest.update(component.read_bytes())
    if value["image"] != "sha256:" + digest.hexdigest():
        raise PermissionError("remote component version changed")
    return value


def require_home() -> None:
    configuration()
    fd = os.open("/home/kai", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (info.st_uid, info.st_gid) != (28000, 28000):
            raise PermissionError("remote task home ownership changed")
    finally:
        os.close(fd)


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError("privileged fixed entrypoint required")
    config = configuration()
    body = sys.stdin.buffer.read(40 * 1024**2 + 1)
    request = json.loads(body)
    if request == {"version": 1, "action": "identity", "spec": {}}:
        print(json.dumps({k: config[k] for k in ("machine_id", "computer_id", "image")}))
        return
    if request.get("action") in {"native_desktop", "native_barrier"}:
        if (
            not config.get("native_desktop")
            or request.keys() != {"version", "action", "spec"}
            or request["version"] != 1
        ):
            raise PermissionError("native desktop is not enabled")
        # configuration() has already verified the root-owned module's digest.
        native = importlib.util.spec_from_file_location(
            "kai_native_desktop", "/opt/kai-computer/native_desktop.py"
        )
        assert native is not None and native.loader is not None
        module = importlib.util.module_from_spec(native)
        native.loader.exec_module(module)
        with module.locked():
            if request["action"] == "native_barrier":
                if request["spec"].keys() != {"epoch"}:
                    raise ValueError("native desktop barrier is invalid")
                module.fence(request["spec"]["epoch"], blocked=True)
                print('{"drained":true}')
            else:
                print(json.dumps(module.run(config, request["spec"]), ensure_ascii=False))
        return
    # Import only a root-owned fixed file; the VM gate owns decoding, spooling,
    # NNP and the supervisor entry. This wrapper changes only the home proof.
    info = GATE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("remote gate is not trusted")
    spec = importlib.util.spec_from_file_location("kai_guest_gate", GATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Reuse its stdin contract without writing an untrusted path.
    import io

    sys.stdin = io.TextIOWrapper(io.BytesIO(body), encoding="utf-8")
    module.run(require_workdisk=require_home)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Computer remote control refused the request.", file=sys.stderr)
        raise SystemExit(125) from None
