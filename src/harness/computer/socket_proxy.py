"""SSH ProxyCommand for a private reverse-forwarded Unix socket, no TCP listener."""

import contextlib
import os
import socket
import sys
import threading


def main() -> None:
    if len(sys.argv) != 2:
        raise ValueError("one private socket is required")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(sys.argv[1])

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
