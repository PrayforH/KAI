import os
import secrets
import subprocess

os.umask(0o077)
subprocess.run(
    ["xauth", "-f", "/run/kai-desktop/Xauthority"],
    input="add :99 . " + secrets.token_hex(16) + "\n",
    text=True,
    check=True,
)
