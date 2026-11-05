# Persistent desktop guest image

These sources build a Debian 12 desktop base with a generic kernel, Chromium,
Openbox, xterm and a private UNIX-socket VNC transport. The image is separate
from a user's root disk, work disk, browser profile and per-VM SSH identities.

`desktop-shell.py` provides a native welcome surface and Dock with real app
launchers. `appearance.py` saves private, atomic theme/wallpaper preferences and
retains other Openbox settings. It does not expose an HTTP launch service.
The Dock is managed by Openbox and reserves its own work area. Chromium keeps
its existing profile and opens only when requested from a launcher.

`build-seed.py` accepts only the listed public build sources and the fixed
control modules. It writes a private NoCloud seed directory without login or
host credentials. Boot it with a verified official Debian base in an isolated
builder; do not attach a user work volume. `build.sh` runs installation, then
schedules sealing after cloud-final. `seal.sh` rejects a non-builder, removes
builder state, verifies the keyless result and powers off. Flatten the stopped
disk, run qemu-img check, and record the file hash and source hashes before
promoting a base.

Create each real VM from a fresh writable disk plus `VmSeedStore` identities.
Keep the store private (0700 directories, 0600 files) and reuse its completed
manifest on retries. Never regenerate a damaged identity silently or bake a
user seed into a reusable base. SSH control and viewing keys have separate
forced commands and pinned host keys.

Boot details that matter:

- Native systemd-networkd configuration is generated from `vm-network.json`;
  cloud-init networking is disabled in the sealed guest to avoid competing
  network managers.
- Keep an empty `/etc/machine-id` inode when sealing. A missing file can make
  first boot fail on this filesystem configuration.
- The desktop waits for cloud-config, not cloud-final. Ordering it after
  cloud-final creates a multi-user boot cycle.
- x11vnc 0.9.16 uses `-unixsock` with IPv4/IPv6 RFB ports disabled;
  `-unixsockonly` is not supported by this packaged version.
- xterm provides the Openbox `x-terminal-emulator` alternative; the desktop
  explicitly sets its shell to bash despite its service account login shell.
- The desktop service fails if the display or VNC process exits. Closing the
  browser alone does not destroy the desktop.

For an existing user's computer, patch or migrate it with retained identity
and disks; never replace its root disk with a newer base as an update shortcut.
Before offline maintenance, disable the runner's automatic restart, request a
normal QMP powerdown, and verify it has exited. Restore the intended restart
policy after maintenance. Preserve owner preview instances and their volumes;
they are no longer disposable build fixtures.

Desktop publication uses `~/deliver` (`/var/lib/kai-desktop/deliver`), created
privately by the desktop preparation step. `computer_export_file` copies an
explicit relative file from that outbox into `outputs/desktop` in the task
workspace. Text, video and other regular files share this path; existing
workspace collection, artifact publishing and channel delivery remain the
file plane. The bridge never scans the desktop HOME or exports browser state.
The current VM capability requires the installed native gate and is not
advertised to legacy enrolled physical computers without that bridge.

The forced export entry reuses execution scope, desktop epoch and the action
lock. It permits regular, single-link desktop-owned files up to 64 MiB,
refuses symlink ancestors, checks source stability and uses private staging
plus atomic no-overwrite publication. Repeating the same file is idempotent;
a different existing file needs a new export name. Output ownership belongs
to the task account: only the validated source descriptor crosses identities;
workdisk writes run with the task effective UID/GID and no supplementary
groups. The dedicated gate briefly restores its saved effective UID only for
lease checks, then restores its initial identity on every exit. This avoids
root credential/chown failures on 9P without desktop HOME permission changes.

The 79 release and GUI acceptance record is in
[`assistant-computer-viewer-79-20261027.md`](../../../../docs/assistant-computer-viewer-79-20261027.md).
The subsequent theme, wallpaper, application and inline split-pane acceptance
is in [`assistant-computer-theme-split-79-20261027.md`](../../../../docs/assistant-computer-theme-split-79-20261027.md).

Desktop-to-workspace text/video bridge release and real browser acceptance:
[`2026-11-05-desktop-workspace-export.md`](../../../../docs/results/2026-11-05-desktop-workspace-export.md).
