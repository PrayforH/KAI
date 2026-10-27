"""Real browser/desktop acceptance inside the isolated Debian KVM probe only.

Run before and after restarting the VM. No production identity, browser login,
working volume or GUI endpoint is used. Results contain only the public marker.
"""

import argparse
import json
import os
import pwd
import signal
import socket
import subprocess
import time
from html.parser import HTMLParser
from pathlib import Path

MARKER = "KAI79-DESKTOP-20261027-X7V2"
PROFILE = Path("/var/lib/kai-desktop")
DISPLAY_ENV = ["DISPLAY=:99", "XAUTHORITY=/run/kai-desktop/Xauthority"]


def command(args: list[str], *, user: str | None = None, timeout: int = 30):
    if user is not None:
        args = ["runuser", "-u", user, "--", "env", *DISPLAY_ENV, *args]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def desktop(*args: str) -> str:
    result = command(list(args), user="kai-desktop")
    if result.returncode:
        raise RuntimeError(f"Desktop probe command failed: {args[0]}")
    return result.stdout.strip()


class SandboxTable(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def sandbox(phase: str) -> dict[str, str]:
    args = [
        "runuser",
        "-u",
        "kai-desktop",
        "--",
        "setpriv",
        "--no-new-privs",
        "chromium",
        "--headless",
        "--allow-chrome-scheme-url",
        "--dump-dom",
        "--timeout=15000",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=" + str(PROFILE / ("sandbox-check-" + phase)),
        "chrome://sandbox",
    ]
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as process:
        try:
            html, errors = process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise RuntimeError("Sandbox self-check timed out") from None
        if process.returncode:
            raise RuntimeError("Sandbox self-check failed: " + errors[-500:])
    table = SandboxTable()
    table.feed(html)
    rows = {row[0]: row[1] for row in table.rows if len(row) == 2}
    diagnostic = Path("/var/tmp/kai-desktop-sandbox-" + phase + ".html")
    diagnostic.write_text(html)
    diagnostic.chmod(0o600)
    if rows.get("Layer 1 Sandbox") != "Namespace" and rows.get("Namespace sandbox") != "Yes":
        raise RuntimeError("Browser did not confirm its namespace sandbox: " + repr(rows))
    for required in (
        "PID namespaces",
        "Network namespaces",
        "Seccomp-BPF sandbox",
        "Seccomp-BPF sandbox supports TSYNC",
    ):
        if rows.get(required) != "Yes":
            raise RuntimeError("Browser sandbox did not confirm " + required + ": " + repr(rows))
    return rows


def processes() -> tuple[int, dict[str, object]]:
    main: list[int] = []
    renderers: list[int] = []
    for path in Path("/proc").glob("[0-9]*"):
        try:
            # Chromium rewrites its process title into one space-separated
            # argv entry. Probe paths/flags are fixed and contain no spaces.
            args = path.joinpath("cmdline").read_bytes().decode().replace("\0", " ").split()
            if (
                not args
                or Path(args[0]).name != "chromium"
                or "--user-data-dir=" + str(PROFILE / "chromium") not in args
            ):
                continue
            status = path.joinpath("status").read_text()
            if "Uid:\t28001\t28001\t28001\t28001" not in status:
                continue
            if not any(arg.startswith("--type=") for arg in args):
                if (
                    "--no-sandbox" in args
                    or "--disable-namespace-sandbox" in args
                    or any(arg.startswith("--remote-debugging-port") for arg in args)
                ):
                    raise RuntimeError("Browser sandbox was disabled")
                main.append(int(path.name))
            elif "--type=renderer" in args:
                renderers.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    if len(main) != 1 or not renderers:
        raise RuntimeError("Expected one desktop browser with active renderers")
    browser = main[0]
    browser_ns = os.readlink(f"/proc/{browser}/ns/pid")
    records: list[dict[str, object]] = []
    for pid in [browser, *renderers]:
        fields = dict(
            line.split(":", 1)
            for line in Path(f"/proc/{pid}/status").read_text().splitlines()
            if ":" in line
        )
        values = {key: fields[key].strip() for key in ("Uid", "CapEff", "NoNewPrivs", "Seccomp")}
        if values["CapEff"] != "0000000000000000" or values["NoNewPrivs"] != "1":
            raise RuntimeError("Browser process privilege check failed")
        isolated = os.readlink(f"/proc/{pid}/ns/pid") != browser_ns
        if pid != browser and (values["Seccomp"] != "2" or not isolated):
            raise RuntimeError("Renderer seccomp or PID namespace check failed")
        records.append(
            {"pid": pid, "renderer": pid != browser, "isolatedPidNamespace": isolated, **values}
        )
    return browser, {"processes": records}


def boundaries(browser: int) -> dict[str, bool]:
    checks = {
        "profileUnreadable": ["test", "-r", str(PROFILE / "chromium/Default/Preferences")],
        "xauthorityUnreadable": ["test", "-r", "/run/kai-desktop/Xauthority"],
        "browserEnvironmentUnreadable": ["test", "-r", f"/proc/{browser}/environ"],
        "browserSignalDenied": ["kill", "-0", str(browser)],
        "x11Denied": ["xdotool", "getdisplaygeometry"],
        "sudoDenied": ["sudo", "-n", "true"],
    }
    result = {name: command(args, user="kai").returncode != 0 for name, args in checks.items()}
    if not all(result.values()):
        raise RuntimeError("Task account crossed the desktop identity boundary")
    with socket.socket() as connection:
        connection.settimeout(2)
        result["noPublicCdp"] = connection.connect_ex(("127.0.0.1", 9222)) != 0
    if not result["noPublicCdp"]:
        raise RuntimeError("Unexpected unauthenticated CDP listener")
    with socket.create_connection(("127.0.0.1", 5900), timeout=3) as connection:
        stream = connection.makefile("rb")
        header = stream.read(12)
        if not header.startswith(b"RFB "):
            raise RuntimeError("VNC protocol was not confirmed")
        connection.sendall(header)
        count = stream.read(1)
        kinds = stream.read(count[0]) if count else b""
        result["vncRequiresAuthentication"] = 2 in kinds and 1 not in kinds
    if not result["vncRequiresAuthentication"]:
        raise RuntimeError("VNC accepted an unauthenticated client")
    return result


def continuity(phase: str) -> dict[str, str]:
    desktop("xdotool", "key", "--clearmodifiers", "ctrl+l")
    desktop("xdotool", "type", "--clearmodifiers", "file:///usr/local/share/kai-desktop/ready.html")
    desktop("xdotool", "key", "Return")
    time.sleep(2)
    if phase == "before":
        desktop("xdotool", "key", "--clearmodifiers", "ctrl+a")
        desktop("xdotool", "type", "--clearmodifiers", "--delay", "2", MARKER)
        time.sleep(0.5)
    desktop("xdotool", "key", "--clearmodifiers", "ctrl+a", "ctrl+c")
    time.sleep(0.3)
    copied = desktop("xclip", "-selection", "clipboard", "-o")
    if copied != MARKER:
        raise RuntimeError("Actual browser input did not retain the expected marker")
    screenshot = PROFILE / ("desktop-" + phase + ".png")
    desktop("scrot", str(screenshot))
    return {"marker": copied, "screenshot": str(screenshot)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["before", "after"], required=True)
    phase = parser.parse_args().phase
    if os.geteuid() != 0 or socket.gethostname() != "kai-desktop-probe":
        raise RuntimeError("Run only as administrator inside the isolated desktop probe VM")
    if pwd.getpwnam("kai").pw_uid != 28000 or pwd.getpwnam("kai-desktop").pw_uid != 28001:
        raise RuntimeError("Probe account configuration does not match")
    if command(["systemctl", "is-active", "--quiet", "kai-desktop-probe"]).returncode:
        raise RuntimeError("Desktop service is not active")
    report: dict[str, object] = {
        "phase": phase,
        "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "kernel": os.uname().release,
        "browserVersion": desktop("chromium", "--version"),
        "sandbox": sandbox(phase),
    }
    browser, details = processes()
    report.update(details)
    report["boundaries"] = boundaries(browser)
    report["continuity"] = continuity(phase)
    result = json.dumps(report, indent=2) + "\n"
    output = Path("/var/tmp/kai-desktop-probe-" + phase + ".json")
    output.write_text(result)
    output.chmod(0o600)
    print(result, end="")


if __name__ == "__main__":
    main()
