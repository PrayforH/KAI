"""Build a maintenance seed for an existing KAI guest disk.

The seed carries code only. Cloud-init keeps the guest's SSH host key, control
and viewer identities, and work disk. Use a fresh instance ID for each
maintenance run and keep the previous disk and seed for rollback.
"""

import argparse
import base64
import json
import re
from pathlib import Path

from harness.computer.vm_seed import _VIEW_COMMAND

CONTROL_DIR = "src/harness/computer"
DESKTOP_DIR = "deploy/assistant-79/computer-vm/guest-image"

FILES = (
    *((f"{CONTROL_DIR}/{name}", f"/opt/kai-computer/{name}", "0644") for name in (
        "computer_control.py", "display_gate.py", "ssh_gate.py", "supervisor.py",
        "acp_bridge.py", "desktop_export.py",
    )),
    (
        f"{DESKTOP_DIR}/desktop-session@.service",
        "/etc/systemd/system/desktop-session@.service",
        "0644",
    ),
    (f"{DESKTOP_DIR}/desktop-shell.py", "/usr/local/lib/kai-desktop/desktop-shell.py", "0644"),
    *((f"{DESKTOP_DIR}/{name}", f"/usr/local/lib/kai-desktop/{name}", "0755") for name in (
        "session-start", "session-body",
    )),
)

# Change only the forced command on the existing viewer key. No new keys are
# minted, and the key is never exposed to the seed builder.
UPDATE_VIEWER_KEY = r"""
import os
import re
import tempfile
from pathlib import Path

target = Path("/var/empty/kai-view/.ssh/authorized_keys")
pattern = r'(restrict,command=")[^"\n]*?/opt/kai-computer/display_gate\.py(" [^\n]+)'
updated, count = re.subn(pattern, lambda match: match[1] + COMMAND + match[2], target.read_text())
if count != 1:
    raise SystemExit("expected exactly one existing forced viewer key")
with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, mode="w") as file:
    file.write(updated)
    os.fchmod(file.fileno(), 0o644)
    temp = file.name
os.replace(temp, target)
"""


def build(source: Path, output: Path, *, instance_id: str) -> None:
    if not re.fullmatch(r"kai-bot-maint-[a-z0-9-]{1,80}", instance_id):
        raise ValueError("maintenance instance ID is invalid")
    output.mkdir(mode=0o700)
    files = [
        {
            "path": target,
            "owner": "root:root",
            "permissions": mode,
            "encoding": "b64",
            "content": base64.b64encode((source / name).read_bytes()).decode("ascii"),
        }
        for name, target, mode in FILES
    ]
    files.append({
        "path": "/etc/sudoers.d/kai-computer-view",
        "owner": "root:root",
        "permissions": "0440",
        "content": (
            "kai-view ALL=(root) NOPASSWD:SETENV: "
            "/usr/bin/python3 -I /opt/kai-computer/display_gate.py\n"
        ),
    })
    config = {
        "users": [],
        "ssh_pwauth": False,
        "disable_root": True,
        "allow_public_ssh_keys": False,
        "ssh_deletekeys": False,
        "ssh_genkeytypes": [],
        "ssh_publish_hostkeys": {"enabled": False},
        "write_files": files,
        "runcmd": [[
            "/bin/sh", "-ec",
            "/usr/sbin/visudo -cf /etc/sudoers.d/kai-computer-view >/dev/null\n"
            "systemctl daemon-reload\n"
            '/usr/bin/python3 -I - <<\'PY\'\n'
            f"COMMAND = {_VIEW_COMMAND!r}\n{UPDATE_VIEWER_KEY}\n"
            "PY\n"
            "systemctl restart ssh.service\n",
        ]],
    }
    (output / "user-data").write_text("#cloud-config\n" + json.dumps(config) + "\n")
    (output / "meta-data").write_text(json.dumps({"instance-id": instance_id}) + "\n")
    (output / "network-config").write_bytes((source / CONTROL_DIR / "vm-network.json").read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    args = parser.parse_args()
    build(args.source, args.output, instance_id=args.instance_id)
