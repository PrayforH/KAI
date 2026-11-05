import os
import secrets
import subprocess
from pathlib import Path

os.umask(0o077)
Path.home().joinpath("deliver").mkdir(mode=0o700, exist_ok=True)
subprocess.run(
    ["xauth", "-f", "/run/kai-desktop/Xauthority"],
    input="add :99 . " + secrets.token_hex(16) + "\n",
    text=True,
    check=True,
)
