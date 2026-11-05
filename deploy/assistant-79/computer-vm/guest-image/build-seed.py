"""Create a credential-free build seed from an explicit source allowlist."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

ASSETS = (
    "install.sh",
    "seal.sh",
    "build.sh",
    "desktop-prepare.py",
    "desktop-stop.py",
    "desktop-start",
    "desktop-shell.py",
    "appearance.py",
    "kai-desktop.service",
    "select-kernel.py",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--control-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    output = Path(args.output)
    output.mkdir(mode=0o700)
    files: list[dict[str, str]] = []
    hashes: dict[str, str] = {}
    for directory, names in (
        (Path(args.runtime_dir), ASSETS),
        (Path(args.control_dir), (
            "supervisor.py", "ssh_gate.py", "display_gate.py", "native_desktop.py",
        )),
    ):
        for name in names:
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("build source must be an ordinary file")
            data = path.read_bytes()
            if len(data) > 1024 * 1024:
                raise ValueError("build source exceeds the allowed size")
            hashes[name] = hashlib.sha256(data).hexdigest()
            files.append(
                {
                    "path": "/opt/kai-image-source/" + name,
                    "owner": "root:root",
                    "permissions": "0644",
                    "encoding": "b64",
                    "content": base64.b64encode(data).decode("ascii"),
                }
            )
    network = json.loads((Path(args.control_dir) / "vm-network.json").read_text())
    interface = network["ethernets"]["kai"]
    networkd = (
        "[Match]\nName=" + interface["match"]["name"] + "\n[Network]\n"
        "Address="
        + interface["addresses"][0]
        + "\nGateway="
        + interface["routes"][0]["via"]
        + "\nDNS="
        + interface["nameservers"]["addresses"][0]
        + "\n"
    )
    files.append(
        {"path": "/etc/systemd/network/10-kai.network", "permissions": "0644", "content": networkd}
    )
    files.append(
        {
            "path": "/etc/kai-image-builder",
            "permissions": "0600",
            "content": "keyless build from verified official base\n",
        }
    )
    config = {
        "hostname": "kai-image-builder",
        "users": [],
        "ssh_pwauth": False,
        "disable_root": True,
        "allow_public_ssh_keys": False,
        "ssh_deletekeys": True,
        "ssh_genkeytypes": [],
        "ssh_quiet_keygen": True,
        "ssh_publish_hostkeys": {"enabled": False},
        "bootcmd": [["systemctl", "mask", "--now", "ssh.service", "ssh.socket"]],
        "write_files": files,
        "runcmd": [
            ["systemctl", "restart", "systemd-networkd"],
            ["bash", "/opt/kai-image-source/build.sh"],
        ],
    }
    (output / "user-data").write_text("#cloud-config\n" + json.dumps(config) + "\n")
    (output / "meta-data").write_text(
        json.dumps({"instance-id": "kai-keyless-build-" + os.urandom(8).hex()}) + "\n"
    )
    (output / "source-sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    network = (Path(args.control_dir) / "vm-network.json").read_bytes()
    (output / "network-config").write_bytes(network)


if __name__ == "__main__":
    main()
