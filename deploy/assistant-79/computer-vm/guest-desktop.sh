#!/bin/bash
# Run as guest root in the disposable, private Debian 12 VM only.
# No user login, production account, or production working disk is loaded here.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = kai-desktop-probe
test "$(. /etc/os-release; printf '%s' "$VERSION_CODENAME")" = bookworm
test "$(id -u kai)" = 28000
test "$(id -u kai-desktop)" = 28001

# Task users must not create an SSH login outside their execution namespace.
cat > /etc/ssh/sshd_config.d/10-kai-private.conf <<'SSH'
PasswordAuthentication no
PermitRootLogin no
AllowUsers kai-admin
AllowAgentForwarding no
X11Forwarding no
AllowTcpForwarding no
SSH
sshd -t
systemctl reload ssh

# Keep Debian archive signature validation; only select a reachable mirror.
python3 - <<'PY'
from pathlib import Path
paths = [Path('/etc/apt/sources.list')]
paths += list(Path('/etc/apt/sources.list.d').glob('*.sources'))
paths += list(Path('/etc/apt/sources.list.d').glob('*.list'))
paths += list(Path('/etc/apt/mirrors').glob('*.list'))
for path in paths:
    if not path.is_file():
        continue
    value = path.read_text()
    for scheme in ('http', 'https'):
        for host in ('deb.debian.org', 'security.debian.org'):
            value = value.replace(scheme + '://' + host + '/debian-security',
                                  'https://mirrors.tuna.tsinghua.edu.cn/debian-security')
        value = value.replace(scheme + '://deb.debian.org/debian',
                              'https://mirrors.tuna.tsinghua.edu.cn/debian')
    path.write_text(value)
PY
apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=10 -o Acquire::https::Timeout=15 update
DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=2 \
  -o Acquire::http::Timeout=10 -o Acquire::https::Timeout=15 install -y --no-install-recommends \
  chromium xvfb openbox x11vnc dbus-x11 xauth xdotool xclip wmctrl scrot \
  tigervnc-tools python3 fonts-noto-cjk

install -d -m 0700 -o kai-desktop -g kai-desktop /var/lib/kai-desktop
chmod 0700 /home/kai-desktop
install -d -m 0755 /usr/local/lib/kai-desktop /usr/local/share/kai-desktop
printf 'kernel.yama.ptrace_scope = 1\n' > /etc/sysctl.d/70-kai-desktop-probe.conf
sysctl -p /etc/sysctl.d/70-kai-desktop-probe.conf
python3 - <<'PY'
import os, pathlib, secrets, subprocess
p = pathlib.Path('/var/lib/kai-desktop/vnc.pass')
if not p.exists():
    # vncpasswd receives the random secret on stdin, never process arguments.
    value = secrets.token_urlsafe(6) + '\n'
    data = subprocess.check_output(['tigervncpasswd', '-f'], input=value.encode())
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.chown(p, 28001, pathlib.Path('/var/lib/kai-desktop').stat().st_gid)
PY
cat > /usr/local/lib/kai-desktop/prepare.py <<'PY'
import os, secrets, subprocess
os.umask(0o077)
subprocess.run(
    ['xauth', '-f', '/run/kai-desktop/Xauthority'],
    input='add :99 . ' + secrets.token_hex(16) + '\n',
    text=True, check=True,
)
PY
cat > /usr/local/lib/kai-desktop/start <<'SH'
#!/bin/bash
set -euo pipefail
export DISPLAY=:99 XAUTHORITY=/run/kai-desktop/Xauthority
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -auth "$XAUTHORITY" &
display_pid=$!
for i in $(seq 1 100); do
  if xdotool getdisplaygeometry >/dev/null 2>&1; then break; fi
  sleep 0.1
done
xdotool getdisplaygeometry
openbox &
x11vnc -display :99 -auth "$XAUTHORITY" -localhost -rfbport 5900 \
  -rfbauth /var/lib/kai-desktop/vnc.pass -forever -shared &
chromium --user-data-dir=/var/lib/kai-desktop/chromium --start-maximized \
  --no-first-run --no-default-browser-check \
  file:///usr/local/share/kai-desktop/ready.html &
printf '%s\n' "$!" > /run/kai-desktop/browser.pid
# Closing the browser does not tear down the computer's display service.
wait "$display_pid"
SH
chmod 0755 /usr/local/lib/kai-desktop/start
cat > /usr/local/lib/kai-desktop/stop-browser.py <<'PY'
import os, select, subprocess
from pathlib import Path

# Keep Xvfb alive while Chromium flushes its profile and marks a clean exit.
# The start script records the browser PID in its private runtime directory;
# Chromium's non-dumpable process title need not be readable by a sibling.
pid = int(Path('/run/kai-desktop/browser.pid').read_text())
try:
    fd = os.pidfd_open(pid)
except ProcessLookupError:
    raise SystemExit(0)
try:
    env = {**os.environ, 'DISPLAY': ':99', 'XAUTHORITY': '/run/kai-desktop/Xauthority'}
    windows = subprocess.check_output(['wmctrl', '-lp'], env=env, text=True).splitlines()
    for line in windows:
        fields = line.split(None, 4)
        if len(fields) >= 3 and fields[2] == str(pid):
            subprocess.run(['wmctrl', '-ic', fields[0]], env=env, check=True)
    if not select.select([fd], [], [], 15)[0]:
        raise RuntimeError('Browser did not finish its graceful shutdown')
finally:
    os.close(fd)
PY
cat > /usr/local/share/kai-desktop/ready.html <<'HTML'
<!doctype html><meta charset="utf-8"><title>KAI Computer 验证</title>
<style>body{font:20px system-ui;background:#111820;color:#edf5fa;margin:80px}p{color:#aac0cc}input{font:inherit;padding:12px;width:500px}small{display:block;margin-top:24px;color:#aac0cc}</style>
<h1>KAI Computer</h1><p>79 · 独立桌面运行验证</p>
<input id="continuity" autofocus placeholder="浏览器重启后保留这段文字">
<small id="state"></small><script>
const field=document.querySelector('#continuity'),state=document.querySelector('#state');
field.value=localStorage.getItem('kai-desktop-probe')||'';
field.addEventListener('input',()=>{localStorage.setItem('kai-desktop-probe',field.value);state.textContent='已保存到此浏览器配置';});
</script>
HTML
cat > /etc/systemd/system/kai-desktop-probe.service <<'UNIT'
[Unit]
Description=KAI isolated desktop capability probe
After=network-online.target
Wants=network-online.target
[Service]
User=kai-desktop
Group=kai-desktop
RuntimeDirectory=kai-desktop
RuntimeDirectoryMode=0700
UMask=0077
Environment=HOME=/home/kai-desktop
ExecStartPre=/usr/bin/python3 /usr/local/lib/kai-desktop/prepare.py
ExecStart=/usr/bin/dbus-run-session -- /usr/local/lib/kai-desktop/start
ExecStop=/usr/bin/python3 /usr/local/lib/kai-desktop/stop-browser.py
Restart=on-failure
RestartSec=3
KillMode=control-group
TimeoutStopSec=25
NoNewPrivileges=true
CapabilityBoundingSet=
ProtectSystem=strict
ReadWritePaths=/var/lib/kai-desktop /run/kai-desktop /home/kai-desktop
PrivateTmp=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable kai-desktop-probe.service
systemctl restart kai-desktop-probe.service
