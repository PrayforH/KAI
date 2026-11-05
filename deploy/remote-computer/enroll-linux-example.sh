#!/bin/bash
# Reference operator enrollment for the authorized 79 pilot. The archive and
# pinned known_hosts are supplied separately; no password or private key is committed.
set -euo pipefail
chmod 600 /tmp/kai-remote-enroll.tar
stage=$(mktemp -d /root/kai-enroll.XXXXXX)
chmod 700 "$stage"
tar -xf /tmp/kai-remote-enroll.tar -C "$stage"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends x11vnc
if ! getent passwd 28000 >/dev/null; then
 groupadd --gid 28000 kai-task
 useradd --uid 28000 --gid 28000 --home-dir /home/kai --shell /usr/sbin/nologin kai-task
fi
install -d -o 28000 -g 28000 -m 700 /home/kai
install -d -m 755 /opt/kai-computer
install -m 644 "$stage"/*.py /opt/kai-computer/
# The forced display gate runs as the enrolled desktop user, not root.
# Keep its declaration readable by that user's primary group on every update.
desktop_gid=$(python3 -c 'import json,pwd,sys; print(pwd.getpwuid(json.load(open(sys.argv[1]))["desktop_uid"]).pw_gid)' "$stage/remote.json")
install -o root -g "$desktop_gid" -m 640 "$stage/remote.json" /etc/kai-computer-remote.json
if ! id kai-control >/dev/null 2>&1; then useradd --system --home-dir /var/lib/kai-control --create-home --shell /bin/sh kai-control; fi
install -d -o kai-control -g kai-control -m 700 /var/lib/kai-control/.ssh
printf 'restrict,command="sudo -n /usr/bin/python3 -I /opt/kai-computer/remote_gate.py" %s\n' "$(cat "$stage/control.pub")" > /var/lib/kai-control/.ssh/authorized_keys
chown kai-control:kai-control /var/lib/kai-control/.ssh/authorized_keys
chmod 600 /var/lib/kai-control/.ssh/authorized_keys
printf 'kai-control ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/kai-computer/remote_gate.py\n' > /etc/sudoers.d/kai-remote-computer
chmod 440 /etc/sudoers.d/kai-remote-computer
visudo -cf /etc/sudoers.d/kai-remote-computer
install -d -o xiaokai -g xiaokai -m 700 /home/xiaokai/.ssh
file=/home/xiaokai/.ssh/authorized_keys
touch "$file"
if ! grep -qF "$(cat "$stage/viewer.pub")" "$file"; then
 printf 'restrict,command="/usr/bin/python3 -I /opt/kai-computer/remote_display_gate.py" %s\n' "$(cat "$stage/viewer.pub")" >> "$file"
fi
chown xiaokai:xiaokai "$file"; chmod 600 "$file"
if ! id kai-remote-link >/dev/null 2>&1; then useradd --system --home-dir /var/lib/kai-remote-link --create-home --shell /usr/sbin/nologin kai-remote-link; fi
install -m 600 -o kai-remote-link -g kai-remote-link "$stage/tunnel" /var/lib/kai-remote-link/identity
install -m 600 -o kai-remote-link -g kai-remote-link /tmp/kai-79-known-hosts /var/lib/kai-remote-link/known_hosts
cat > /etc/systemd/system/kai-remote-display.service <<'UNIT'
[Unit]
Description=KAI current desktop private RFB transport
After=display-manager.service
[Service]
User=xiaokai
Group=xiaokai
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/xiaokai/.Xauthority
ExecStartPre=/usr/bin/mkdir -p /run/user/1000/kai-remote
ExecStartPre=/usr/bin/chmod 700 /run/user/1000/kai-remote
ExecStartPre=-/usr/bin/rm /run/user/1000/kai-remote/vnc.sock
ExecStart=/usr/bin/x11vnc -display :0 -auth /home/xiaokai/.Xauthority -unixsock /run/user/1000/kai-remote/vnc.sock -rfbport 0 -rfbportv6 0 -noipv6 -forever -shared -noxdamage -repeat -quiet
Restart=always
RestartSec=4
UMask=0077
[Install]
WantedBy=graphical.target
UNIT
cat > /etc/systemd/system/kai-remote-link.service <<'UNIT'
[Unit]
Description=KAI reverse private computer transport to 79
After=network-online.target
Wants=network-online.target
[Service]
User=kai-remote-link
Group=kai-remote-link
ExecStart=/usr/bin/ssh -NT -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/var/lib/kai-remote-link/known_hosts -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -i /var/lib/kai-remote-link/identity -R /data/kai-remote-20261103/transport/ssh.sock:127.0.0.1:22 kai-remote-link@172.20.105.79
Restart=always
RestartSec=5
NoNewPrivileges=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now kai-remote-display kai-remote-link
systemctl is-active kai-remote-display kai-remote-link
rm -rf "$stage"
rm -f /tmp/kai-remote-enroll.tar
