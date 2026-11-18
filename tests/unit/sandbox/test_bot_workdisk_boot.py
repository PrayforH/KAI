"""The Bot workdisk unit must come back on its own after a guest reboot.

Desktops are disabled when stopped, and the workdisk mount used to be pulled in
only by a running desktop unit. After "last desktop stopped + guest reboot" no
unit mounted /home/kai-bots, so every desktop ensure/start was refused.
"""

from pathlib import Path

IMAGE = Path(__file__).resolve().parents[3] / "deploy/assistant-79/computer-vm/guest-image"


def test_workdisk_unit_can_be_enabled_at_boot() -> None:
    text = (IMAGE / "kai-bot-workdisk.service").read_text()

    assert "[Install]" in text
    assert "WantedBy=multi-user.target" in text


def test_installer_enables_the_workdisk_unit() -> None:
    text = (IMAGE / "install.sh").read_text()

    assert "systemctl enable kai-bot-workdisk.service" in text


def test_desktop_sessions_still_require_the_workdisk() -> None:
    text = (IMAGE / "desktop-session@.service").read_text()

    assert "Requires=kai-bot-workdisk.service" in text
