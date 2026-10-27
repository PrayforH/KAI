"""Forced SSH command: relay only the desktop user's private Unix RFB socket.

No shell, TCP forwarding, root privileges, paths or commands from the client.
Node keys plus KAI's authenticated WebSocket protect this transport. The RFB
server has no TCP listener and its socket is inside a mode-0700 directory.
"""

import contextlib
import ctypes
import os
import socket
import sys
import threading

SOCKET = "/run/kai-desktop/vnc.sock"


def main() -> None:
    if sys.platform != "linux" or os.geteuid() != 28001 or len(sys.argv) != 1:
        raise PermissionError("private desktop transport required")
    if ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0) != 0:
        raise PermissionError("private desktop transport could not drop privileges")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(SOCKET)

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
