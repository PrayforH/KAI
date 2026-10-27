"""Physical supervisor compatibility in the private desktop VM, without a model/DB Run."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

SUPERVISOR = Path("/opt/kai-computer/supervisor.py")
SOURCE_SHA256 = "41b0880eca6d15c5a9de8587868c66d7b9bef3dc647fe65e2ce8b15f0783cf64"
ROOT = Path("/var/lib/kai-computer")
WORK = Path("/home/kai/workspace/vm-pilot")
MARKER = "KAI79-VM-TERMINAL-20261027-Q9K4"


def action(name: str, token: dict[str, object], **payload: object):
    request = ROOT / "spool" / (uuid4().hex + ".json")
    fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({**token, **payload}, stream)
    return subprocess.run(
        ["setpriv", "--no-new-privs", sys.executable, "-I", str(SUPERVISOR), name, str(request)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


PROGRAM = r"""
import hashlib, json, os, pathlib, subprocess, sys, tempfile, time

root = pathlib.Path("vm-pilot")
root.mkdir(exist_ok=True)
identity = root / "identity.txt"
marker = "KAI79-VM-TERMINAL-20261027-Q9K4"
if sys.argv[1] == "write":
    if not identity.exists():
        identity.write_text(marker + "\n")
        identity.chmod(0o600)
    tool = pathlib.Path.home() / "bin/kai79-vm-pilot"
    if not tool.exists():
        tool.write_text('#!/bin/sh\nprintf "%s\\n" ' + marker + "\n")
        tool.chmod(0o700)
assert identity.read_text().strip() == marker
actual = subprocess.check_output(
    [str(pathlib.Path.home() / "bin/kai79-vm-pilot")], text=True
).strip()
assert actual == marker
lines = pathlib.Path("/proc/self/status").read_text().splitlines()
status = dict(line.split(":", 1) for line in lines if ":" in line)
assert os.getuid() == 28000 and os.getgid() == 28000
assert status["NoNewPrivs"].strip() == "1" and status["CapEff"].strip() == "0000000000000000"
try:
    open("/var/lib/kai-desktop/chromium/Default/Preferences").read()
    raise AssertionError("desktop profile readable")
except PermissionError:
    pass
visible_desktop = False
for path in pathlib.Path("/proc").glob("[0-9]*/status"):
    try:
        if "Uid:\t28001\t" in path.read_text():
            visible_desktop = True
    except (PermissionError, FileNotFoundError):
        pass
assert not visible_desktop
# An SSH login would escape this Run's PID namespace. Try a genuine newly
# authorized key, then restore the original file even if the assertion fails.
auth = pathlib.Path.home() / ".ssh/authorized_keys"
auth.parent.mkdir(mode=0o700, exist_ok=True)
old = auth.read_bytes() if auth.exists() else None
try:
    with tempfile.TemporaryDirectory(prefix="ssh-fence-", dir=str(root)) as temp:
        key = pathlib.Path(temp) / "key"
        known = pathlib.Path(temp) / "known_hosts"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        auth.write_bytes(key.with_suffix(".pub").read_bytes())
        auth.chmod(0o600)
        hostkey = sys.argv[2]
        known.write_text("kai-vm-probe-guest " + hostkey)
        attempt = subprocess.run(
            [
                "ssh",
                "-i",
                str(key),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "UserKnownHostsFile=" + str(known),
                "-o",
                "HostKeyAlias=kai-vm-probe-guest",
                "-o",
                "ConnectTimeout=3",
                "kai@127.0.0.1",
                "id",
                "-u",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        assert attempt.returncode == 255 and "Permission denied" in attempt.stderr
finally:
    if old is None:
        auth.unlink(missing_ok=True)
    else:
        auth.write_bytes(old)
if sys.argv[1] == "write":
    daemon = (
        "import pathlib,time\np=pathlib.Path('vm-pilot/heartbeat.txt')\n"
        "while True:\n p.write_text(str(time.time_ns()));time.sleep(0.05)"
    )
    subprocess.Popen(
        ["python3", "-I", "-c", daemon],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
print(
    json.dumps(
        {
            "uid": os.getuid(),
            "gid": os.getgid(),
            "noNewPrivs": True,
            "capabilitiesEmpty": True,
            "browserProfileDenied": True,
            "desktopOutsideRunNamespace": True,
            "sshLoginDenied": True,
            "marker": actual,
            "identitySha256": hashlib.sha256(identity.read_bytes()).hexdigest(),
        }
    )
)
"""


def main() -> None:
    if os.geteuid() != 0 or socket.gethostname() != "kai-desktop-probe":
        raise RuntimeError("Run only in the isolated desktop probe VM")
    if hashlib.sha256(SUPERVISOR.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Supervisor source is not the verified version")
    subprocess.run(
        ["setpriv", "--no-new-privs", sys.executable, "-I", str(SUPERVISOR), "prepare"],
        check=True,
        capture_output=True,
    )
    state_file = ROOT / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {"epoch": 0}
    if state.get("active"):
        raise RuntimeError("An execution still owns the isolated VM")
    epoch = int(state["epoch"])

    def token() -> dict[str, object]:
        nonlocal epoch
        epoch += 1
        return {
            "epoch": epoch,
            "lease_id": "computer_lease_" + uuid4().hex,
            "expires_at": time.time() + 120,
        }

    initialization = action("init", token())
    initialization.check_returncode()
    browser = Path("/run/kai-desktop/browser.pid").read_text().strip()
    hostkey = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().strip()
    results: list[object] = []
    previous: dict[str, object] = {}
    identity_before = None
    for phase in ("write", "read"):
        current = token()
        action("activate", current).check_returncode()
        try:
            result = action(
                "exec",
                current,
                argv=["python3", "-I", "-c", PROGRAM, phase, hostkey],
                cwd="/home/kai/workspace",
                timeout_seconds=20,
            )
            if result.returncode:
                raise RuntimeError("Guest execution failed: " + result.stderr[-1500:])
            results.append(json.loads(result.stdout))
        finally:
            action("deactivate", current).check_returncode()
        identity = WORK / "identity.txt"
        signature = (identity.stat().st_mtime_ns, hashlib.sha256(identity.read_bytes()).hexdigest())
        if phase == "write":
            identity_before = signature
            heartbeat = WORK / "heartbeat.txt"
            stopped = heartbeat.read_bytes()
            time.sleep(0.3)
            if heartbeat.read_bytes() != stopped:
                raise RuntimeError("Detached Run process survived deactivation")
            previous = current
        elif signature != identity_before:
            raise RuntimeError("Second execution rewrote the first execution's file")
    rejected = WORK / ("rejected-" + uuid4().hex)
    stale = action(
        "exec",
        previous,
        argv=["touch", str(rejected)],
        cwd="/home/kai/workspace",
        timeout_seconds=10,
    )
    if stale.returncode != 125 or rejected.exists():
        raise RuntimeError("Old execution token was not fenced")
    if Path("/run/kai-desktop/browser.pid").read_text().strip() != browser:
        raise RuntimeError("Run cleanup replaced the persistent browser")
    os.kill(int(browser), 0)
    report = {
        "canonicalAuthorizationTested": False,
        "supervisorSha256": SOURCE_SHA256,
        "runs": results,
        "detachedProcessStopped": True,
        "oldTokenRejected": True,
        "browserPidUnchanged": True,
        "identityMtimeUnchanged": True,
        "marker": MARKER,
    }
    output = json.dumps(report, indent=2) + "\n"
    Path("/var/tmp/kai-vm-execution-probe.json").write_text(output)
    print(output, end="")


if __name__ == "__main__":
    main()
