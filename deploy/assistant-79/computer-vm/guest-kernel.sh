#!/bin/bash
# Select the signed Debian generic kernel required by the private 9P probe.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = kai-desktop-probe
python3 - <<'PY'
from pathlib import Path
import json
p = Path('/var/lib/kai-computer/state.json')
if p.exists() and json.loads(p.read_text()).get('active'):
    raise RuntimeError('stop the current execution before kernel maintenance')
PY
DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=2 \
  -o Acquire::https::Timeout=15 install -y --no-install-recommends linux-image-amd64
python3 - <<'PY'
from pathlib import Path
import json, os, re, shlex, subprocess

dependency = subprocess.check_output(
    ['dpkg-query', '-W', '-f', '${Depends}', 'linux-image-amd64'], text=True)
kernel = re.search(r'linux-image-([0-9][^ ,()]*)', dependency)
if kernel is None:
    raise RuntimeError('generic kernel package could not be identified')
version = kernel.group(1)
text = Path('/boot/grub/grub.cfg').read_text()
submenu = re.search(
    r"submenu 'Advanced options for Debian GNU/Linux'.*?\$menuentry_id_option '([^']+)'", text)
entry = re.search(
    r"menuentry 'Debian GNU/Linux, with Linux " + re.escape(version)
    + r"'.*?\$menuentry_id_option '([^']+)'", text)
if submenu is None or entry is None:
    raise RuntimeError('generic kernel boot entry could not be identified')
# The cloud variant at the same ABI can otherwise sort before generic.
target = submenu.group(1) + '>' + entry.group(1)
Path('/etc/default/grub.d/70-kai-generic.cfg').write_text(
    'GRUB_DEFAULT=' + shlex.quote(target) + '\n')
subprocess.run(['update-grub'], check=True)
print(json.dumps({'selectedKernel': version,
                  'rebootRequiredBefore9p': os.uname().release != version}))
PY
