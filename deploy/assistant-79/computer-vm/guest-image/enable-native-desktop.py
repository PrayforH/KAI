"""Operator-only update of an existing VM; retain its disks, profile and identities.

The payload is a private, read-only maintenance ISO. No account, SSH key or
business file is created here. The native capability remains a separate node
and Worker rollout flag after this guest update succeeds.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

FILES = {
    "agent-desktop": ("/opt/kai-agent-desktop/agent-desktop", 0o755),
    "LICENSE": ("/opt/kai-agent-desktop/LICENSE", 0o644),
    "native_desktop.py": ("/opt/kai-computer/native_desktop.py", 0o644),
    "ssh_gate.py": ("/opt/kai-computer/ssh_gate.py", 0o644),
    "desktop-start": ("/usr/local/lib/kai-desktop/start", 0o755),
    "desktop-shell.py": ("/usr/local/lib/kai-desktop/desktop-shell.py", 0o644),
    "kai-desktop.service": ("/etc/systemd/system/kai-desktop.service", 0o644),
}
CONFIG = Path("/etc/kai-computer/native-desktop.json")


def install(payload: Path) -> None:
    if os.geteuid() != 0 or not Path("/etc/kai-computer/identity.json").is_file():
        raise PermissionError("requires an existing KAI VM and its operator")
    manifest = json.loads((payload / "manifest.json").read_bytes())
    if manifest.keys() != FILES.keys():
        raise ValueError("maintenance payload has an unexpected file set")
    for name, digest in manifest.items():
        path = payload / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("maintenance payload is not an ordinary file: " + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("maintenance payload digest changed")
    backup = Path("/var/backups") / ("kai-native-desktop-" + str(time.time_ns()))
    backup.mkdir(mode=0o700)
    prior: dict[str, int | None] = {}
    targets = [Path(path) for path, _ in FILES.values()] + [CONFIG]
    for index, target in enumerate(targets):
        if target.exists():
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                raise PermissionError("existing operator file is not trusted")
            shutil.copyfile(target, backup / str(index))
            (backup / str(index)).chmod(0o600)
            prior[str(target)] = stat.S_IMODE(info.st_mode)
        else:
            prior[str(target)] = None
    (backup / "manifest.json").write_text(json.dumps(prior))
    # Keep the distribution's own AT-SPI version. Do not add a foreign distro
    # repository, replace glibc, or promise compatibility before real UI tests.
    subprocess.run(["apt-get", "-o", "Acquire::Retries=1", "-o",
                    "Acquire::https::Timeout=15", "update"], check=True,
                   env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "at-spi2-core"],
                   check=True, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    try:
        for name, (target_name, mode) in FILES.items():
            target = Path(target_name)
            target.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
            stage = target.with_name(target.name + ".native-next")
            shutil.copyfile(payload / name, stage)
            stage.chmod(mode)
            os.replace(stage, target)
        CONFIG.write_text(json.dumps({"enabled": True, "binary_sha256": manifest["agent-desktop"]}))
        CONFIG.chmod(0o600)
        subprocess.run(["/opt/kai-agent-desktop/agent-desktop", "version"], check=True,
                       stdout=subprocess.DEVNULL, timeout=10)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", "kai-desktop.service"], check=True, timeout=40)
        receipt = {"installed": True, "binary_sha256": manifest["agent-desktop"],
                   "backup": str(backup), "source_sha256": manifest}
        Path("/var/lib/kai-computer/native-install.json").write_text(json.dumps(receipt))
        print("KAI_VM_NATIVE_INSTALL_OK", flush=True)
    except BaseException:
        for index, target in enumerate(targets):
            mode = prior[str(target)]
            if mode is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copyfile(backup / str(index), target)
                target.chmod(mode)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "restart", "kai-desktop.service"], check=False, timeout=40)
        print("KAI_VM_NATIVE_INSTALL_ROLLED_BACK", flush=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    install(parser.parse_args().payload)
