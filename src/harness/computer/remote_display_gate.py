"""Relay only the enrolled desktop user's private RFB Unix socket over SSH."""

import contextlib
import json
import os
import socket
import stat
import sys
import threading
from pathlib import Path


def main() -> None:
    path = Path("/etc/kai-computer-remote.json")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise PermissionError("untrusted remote desktop declaration")
    config = json.loads(path.read_bytes())
    if os.geteuid() != config["desktop_uid"] or len(sys.argv) != 1:
        raise PermissionError("wrong desktop identity")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(config["display_socket"])

        def upstream() -> None:
            try:
                while data := os.read(0, 65536):
                    channel.sendall(data)
            finally:
                with contextlib.suppress(OSError):
                    channel.shutdown(socket.SHUT_WR)

        threading.Thread(target=upstream, daemon=True).start()
        while data := channel.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
