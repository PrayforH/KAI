import os
import select
import subprocess
from pathlib import Path

# Keep Xvfb alive while Chromium flushes its profile and marks a clean exit.
# The start script records the browser PID in its private runtime directory;
# Chromium's non-dumpable process title need not be readable by a sibling.
pid = int(Path("/run/kai-desktop/browser.pid").read_text())
try:
    fd = os.pidfd_open(pid)
except ProcessLookupError:
    raise SystemExit(0) from None
try:
    env = {**os.environ, "DISPLAY": ":99", "XAUTHORITY": "/run/kai-desktop/Xauthority"}
    windows = subprocess.check_output(["wmctrl", "-lp"], env=env, text=True).splitlines()
    for line in windows:
        fields = line.split(None, 4)
        if len(fields) >= 3 and fields[2] == str(pid):
            subprocess.run(["wmctrl", "-ic", fields[0]], env=env, check=True)
    if not select.select([fd], [], [], 15)[0]:
        raise RuntimeError("Browser did not finish its graceful shutdown")
finally:
    os.close(fd)
