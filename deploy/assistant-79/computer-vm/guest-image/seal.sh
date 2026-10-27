#!/bin/bash
# Run after cloud-final in a builder that never received login credentials.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = kai-image-builder
test -f /etc/kai-image-builder
if mountpoint -q /home/kai; then
  echo 'Refusing to seal an attached user work disk.' >&2
  exit 1
fi
test ! -e /etc/kai-computer/identity.json
test ! -e /var/lib/kai-computer/state.json
test -f /usr/local/share/kai-computer/packages.tsv
test -z "$(find /home/kai /var/lib/kai-desktop -mindepth 1 -print -quit)"
test -z "$(find /etc/ssh -maxdepth 1 -name 'ssh_host_*' -print -quit)"
if id kai-builder >/dev/null 2>&1; then
  echo 'Interactive builder images must not be published.' >&2
  exit 1
fi

systemctl stop ssh.service ssh.socket 2>/dev/null || true
if id debian >/dev/null 2>&1; then userdel --remove debian; fi
rm -f /etc/sudoers.d/90-cloud-init-users /etc/sudoers.d/kai-builder
rm -f /etc/ssh/sshd_config.d/10-kai-builder.conf
cat > /etc/ssh/sshd_config.d/10-kai-computer.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers kai-control kai-view
AllowAgentForwarding no
X11Forwarding no
AllowTcpForwarding no
PermitTunnel no
PermitUserEnvironment no
SSH
install -d -m 0755 /run/sshd
# Host keys deliberately do not exist yet. Each clone's real SSH handshake
# validates this configuration only after its own key has been provisioned.
systemctl unmask ssh.service ssh.socket
systemctl enable ssh.service
systemctl stop systemd-random-seed.service
rm -f /etc/ssh/ssh_host_* /var/lib/systemd/random-seed
rm -rf /root/.ssh /root/.cache /root/.bash_history /var/lib/dhcp/*
rm -rf /tmp/* /var/tmp/*
cloud-init clean --logs --machine-id --seed
# Debian mounts the root read-only before systemd; retain an empty inode.
install -m 0444 /dev/null /etc/machine-id
rm -rf /opt/kai-image-source
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
find /var/log -type f -exec truncate -s 0 {} +
rm -f /etc/kai-image-builder /root/kai-seal-image
printf '1\n' > /usr/local/share/kai-computer/sealed-version
python3 -I - <<'PY'
from pathlib import Path
import json, pwd
checks = {
    'builderAccountRemoved': all(p.pw_name not in {'kai-builder', 'debian'} for p in pwd.getpwall()),
    'hostKeysAbsent': not list(Path('/etc/ssh').glob('ssh_host_*')),
    'rootAuthorizedKeysAbsent': not Path('/root/.ssh').exists(),
    'controlKeyAbsent': not Path('/var/empty/kai-control/.ssh/authorized_keys').exists(),
    'viewerKeyAbsent': not Path('/var/empty/kai-view/.ssh/authorized_keys').exists(),
    'cloudInstancesAbsent': not Path('/var/lib/cloud/instances').exists(),
    'computerStateAbsent': not Path('/var/lib/kai-computer/state.json').exists(),
    'browserProfileAbsent': not list(Path('/var/lib/kai-desktop').iterdir()),
    'workDirectoryEmpty': not list(Path('/home/kai').iterdir()),
    'instanceIdentityAbsent': not Path('/etc/kai-computer/identity.json').exists(),
    'machineIdReset': not Path('/etc/machine-id').exists()
        or Path('/etc/machine-id').read_text().strip() in {'', 'uninitialized'},
}
if not all(checks.values()):
    raise RuntimeError('The disposable builder was not fully cleaned')
Path('/usr/local/share/kai-computer/seal-report.json').write_text(json.dumps(checks) + '\n')
PY
sync
# Remove freed builder key/cache blocks before flattening the stopped disk.
fstrim --verbose /
systemctl poweroff
