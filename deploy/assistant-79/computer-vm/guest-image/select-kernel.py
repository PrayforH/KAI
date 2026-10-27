import json
import os
import re
import shlex
import subprocess
from pathlib import Path

dependency = subprocess.check_output(
    ["dpkg-query", "-W", "-f", "${Depends}", "linux-image-amd64"], text=True
)
kernel = re.search(r"linux-image-([0-9][^ ,()]*)", dependency)
if kernel is None:
    raise RuntimeError("generic kernel package could not be identified")
version = kernel.group(1)
text = Path("/boot/grub/grub.cfg").read_text()
submenu = re.search(
    r"submenu 'Advanced options for Debian GNU/Linux'.*?\$menuentry_id_option '([^']+)'", text
)
entry = re.search(
    r"menuentry 'Debian GNU/Linux, with Linux "
    + re.escape(version)
    + r"'.*?\$menuentry_id_option '([^']+)'",
    text,
)
if submenu is None or entry is None:
    raise RuntimeError("generic kernel boot entry could not be identified")
# The cloud variant at the same ABI can otherwise sort before generic.
target = submenu.group(1) + ">" + entry.group(1)
Path("/etc/default/grub.d/70-kai-generic.cfg").write_text(
    "GRUB_DEFAULT=" + shlex.quote(target) + "\n"
)
subprocess.run(["update-grub"], check=True)
print(
    json.dumps({"selectedKernel": version, "rebootRequiredBefore9p": os.uname().release != version})
)
