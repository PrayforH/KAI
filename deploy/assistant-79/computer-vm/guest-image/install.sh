#!/bin/bash
# Build only from a fresh, verified Debian cloud image, never a user OS disk.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = kai-image-builder
test -f /etc/kai-image-builder
test "$(. /etc/os-release; printf '%s' "$VERSION_CODENAME")" = bookworm
cd -- "$(dirname -- "$0")"
test -f supervisor.py
test -f ssh_gate.py
test -f display_gate.py
command -v sshd >/dev/null
if mountpoint -q /home/kai; then
  echo 'A builder must not mount a user work disk.' >&2
  exit 1
fi
test ! -e /var/lib/kai-computer/state.json

# Retain Debian's signature verification while using a reachable HTTPS mirror.
python3 - <<'PY'
from pathlib import Path
paths = [Path('/etc/apt/sources.list')]
for pattern in ['sources.list.d/*.sources', 'sources.list.d/*.list', 'mirrors/*.list']:
    paths.extend(Path('/etc/apt').glob(pattern))
for path in paths:
    if not path.is_file():
        continue
    text = path.read_text()
    for scheme in ['http', 'https']:
        for host in ['deb.debian.org', 'security.debian.org']:
            text = text.replace(scheme + '://' + host + '/debian-security',
                                'https://mirrors.tuna.tsinghua.edu.cn/debian-security')
        text = text.replace(scheme + '://deb.debian.org/debian',
                            'https://mirrors.tuna.tsinghua.edu.cn/debian')
    path.write_text(text)
PY
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=15 update
apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=15 install -y --no-install-recommends \
  linux-image-amd64 chromium xvfb openbox xterm x11vnc dbus-x11 at-spi2-core xauth xdotool xclip wmctrl \
  tigervnc-tools python3 python3-venv python3-pip python3-tk python3-pil python3-pil.imagetk \
  thunar mousepad x11-utils fonts-noto-cjk sudo \
  git curl wget bash tar unzip zip ca-certificates ffmpeg pandoc poppler-utils
python3 -I select-kernel.py

for entry in 'kai:28000:/home/kai:/bin/bash' \
  'kai-desktop:28001:/var/lib/kai-desktop:/usr/sbin/nologin' \
  'kai-control:28002:/var/empty/kai-control:/bin/sh' \
  'kai-view:28003:/var/empty/kai-view:/bin/sh'; do
  IFS=: read -r account identifier home_directory login_shell <<< "$entry"
  if ! getent group "$account" >/dev/null; then groupadd --gid "$identifier" "$account"; fi
  if ! id "$account" >/dev/null 2>&1; then
    useradd --uid "$identifier" --gid "$identifier" --no-create-home \
      --home-dir "$home_directory" --shell "$login_shell" "$account"
  fi
  test "$(id -u "$account")" = "$identifier"
  test "$(id -g "$account")" = "$identifier"
done
install -d -m 0755 -o root -g root \
  /var/empty/kai-control/.ssh /var/empty/kai-view/.ssh /opt/kai-computer
install -d -m 0700 -o root -g root /var/lib/kai-computer /var/lib/kai-computer/spool
install -d -m 0700 -o kai-desktop -g kai-desktop /var/lib/kai-desktop
# The unmounted path is deliberately not writable by the task user.
install -d -m 0755 -o root -g root /home/kai /etc/kai-computer
# Bot desktops live outside the task user's home and outside the work disk.
install -d -m 0755 -o root -g root /home/kai-bots /etc/kai-desktop
install -d -m 0700 -o root -g root /etc/kai-desktop/sessions
install -m 0644 supervisor.py ssh_gate.py display_gate.py native_desktop.py desktop_export.py \
  computer_control.py /opt/kai-computer/
install -d -m 0755 /usr/local/lib/kai-desktop
install -m 0644 desktop-prepare.py /usr/local/lib/kai-desktop/prepare.py
install -m 0644 desktop-stop.py /usr/local/lib/kai-desktop/stop-browser.py
install -m 0644 appearance.py desktop-shell.py /usr/local/lib/kai-desktop/
install -m 0755 desktop-start /usr/local/lib/kai-desktop/start
install -m 0755 session-start session-body /usr/local/lib/kai-desktop/
install -m 0644 kai-desktop.service desktop-session@.service /etc/systemd/system/
printf 'kai-control ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/kai-computer/ssh_gate.py\n' \
  > /etc/sudoers.d/kai-computer-control
chmod 0440 /etc/sudoers.d/kai-computer-control
visudo -cf /etc/sudoers.d/kai-computer-control >/dev/null
printf 'kai-view ALL=(root) NOPASSWD:SETENV: /usr/bin/python3 -I /opt/kai-computer/display_gate.py\n' \
  > /etc/sudoers.d/kai-computer-view
chmod 0440 /etc/sudoers.d/kai-computer-view
visudo -cf /etc/sudoers.d/kai-computer-view >/dev/null
printf 'kernel.yama.ptrace_scope = 1\n' > /etc/sysctl.d/70-kai-desktop.conf
if ! grep -q '^kai_home ' /etc/fstab; then printf '%s\n' \
  'kai_home /home/kai 9p trans=virtio,version=9p2000.L,msize=262144,cache=none,nodev,nosuid,nofail,x-systemd.mount-timeout=10 0 0' \
  >> /etc/fstab; fi
cat > /etc/cloud/cloud.cfg.d/80-kai-computer.cfg <<'CLOUD'
datasource_list: [NoCloud]
# QEMU user networking is provisioned by the immutable native networkd unit.
network: {config: disabled}
users: []
ssh_pwauth: false
disable_root: true
allow_public_ssh_keys: false
ssh_quiet_keygen: true
ssh_publish_hostkeys:
  enabled: false
CLOUD
rm -f /etc/netplan/50-cloud-init.yaml
systemctl daemon-reload
systemctl enable kai-desktop.service

# Inventory records what is supported; old HOME venvs are not made portable.
install -d -m 0755 /usr/local/share/kai-computer
dpkg-query -W -f='${Package}\t${Version}\n' > /usr/local/share/kai-computer/packages.tsv
sha256sum /opt/kai-computer/*.py > /usr/local/share/kai-computer/source.sha256
install -m 0700 seal.sh /root/kai-seal-image
printf '%s\n' 'Runtime installed. Invoke sealing only from the disposable builder.'
