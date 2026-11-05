#!/bin/bash
# Private VM proof only. The host retains the corresponding private SSH key.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = kai-desktop-probe
test "$#" = 1
test -f /opt/kai-computer/supervisor.py
test -f /opt/kai-computer/ssh_gate.py
ssh-keygen -l -f "$1" >/dev/null
python3 - <<'PY'
from pathlib import Path
import stat
for name in ['/opt/kai-computer', '/opt/kai-computer/supervisor.py',
             '/opt/kai-computer/ssh_gate.py', '/var/empty',
             '/var/empty/kai-control', '/var/empty/kai-control/.ssh']:
    p = Path(name)
    if not p.exists() and not p.is_symlink():
        continue
    s = p.lstat()
    if s.st_uid != 0 or s.st_mode & 0o022 or stat.S_ISLNK(s.st_mode):
        raise PermissionError('control code and key directories must be root-owned')
PY

if ! getent group kai-control >/dev/null; then
  groupadd --gid 28002 kai-control
fi
if ! id kai-control >/dev/null 2>&1; then
  useradd --uid 28002 --gid 28002 --no-create-home \
    --home-dir /var/empty/kai-control --shell /bin/sh kai-control
fi
test "$(id -u kai-control)" = 28002
test "$(id -g kai-control)" = 28002
# Neither the task nor the control login user can replace its authorized key
# or remove the forced-command/restrict options. The key itself is public.
install -d -m 0755 -o root -g root /var/empty/kai-control/.ssh
python3 - "$1" <<'PY'
from pathlib import Path
import os, stat, sys
lines = Path(sys.argv[1]).read_text().strip().splitlines()
if len(lines) != 1 or not lines[0].startswith('ssh-ed25519 '):
    raise ValueError('one Ed25519 public key is required')
key = ' '.join(lines[0].split()[:2])
command = 'sudo -n /usr/bin/python3 -I /opt/kai-computer/ssh_gate.py'
p = Path('/var/empty/kai-control/.ssh/authorized_keys')
if p.exists() or p.is_symlink():
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise PermissionError('control authorized key must be root-owned')
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
with os.fdopen(fd, 'w') as stream:
    stream.write('restrict,command="' + command + '" ' + key + '\n')
os.chmod(p, 0o644)
PY
cat > /etc/sudoers.d/kai-computer-control <<'SUDO'
kai-control ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/kai-computer/ssh_gate.py
SUDO
chmod 0440 /etc/sudoers.d/kai-computer-control
visudo -cf /etc/sudoers.d/kai-computer-control >/dev/null
printf 'AllowUsers kai-control\n' > /etc/ssh/sshd_config.d/20-kai-control.conf
sshd -t
systemctl reload ssh
