"""Appearance changes must survive reload without replacing other user config."""

import importlib.util
import json
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[3]
    / "deploy/assistant-79/computer-vm/guest-image/appearance.py"
)
SPEC = importlib.util.spec_from_file_location("desktop_appearance", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Appearance = MODULE.Appearance


def test_reload_retains_appearance_and_extensions(tmp_path: Path) -> None:
    settings = Appearance(tmp_path)
    settings.path.write_text(
        json.dumps({"theme": "orchid", "wallpaper": "sky", "future": {"keep": True}})
    )
    settings.save("dark", "night")
    reloaded = Appearance(tmp_path).load()
    assert reloaded["theme"] == "dark"
    assert reloaded["wallpaper"] == "night"
    assert reloaded["future"] == {"keep": True}
    assert stat.S_IMODE(settings.path.stat().st_mode) == 0o600
    assert not list(settings.directory.glob(".kai-*"))


@pytest.mark.parametrize(
    "theme,background",
    [("../../theme", "sky"), ("light", "file:///etc/passwd"), ("light", "custom")],
)
def test_rejects_unavailable_or_unrecognized_values(
    tmp_path: Path, theme: str, background: str
) -> None:
    settings = Appearance(tmp_path)
    with pytest.raises(ValueError):
        settings.save(theme, background)
    assert not settings.path.exists()


def test_openbox_change_preserves_bindings_and_original(tmp_path: Path) -> None:
    settings = Appearance(tmp_path)
    path = tmp_path / ".config/openbox/rc.xml"
    path.parent.mkdir(parents=True)
    original = (
        b'<openbox_config xmlns="http://openbox.org/3.4/rc"><theme><name>Original</name></theme>'
        b'<keyboard><keybind key="W-t"><action name="Execute"><command>my-tool</command>'
        b"</action></keybind></keyboard></openbox_config>"
    )
    path.write_bytes(original)
    settings.save("orchid", "orchid")
    settings.save("light", "sage")
    namespace = {"o": "http://openbox.org/3.4/rc"}
    root = ET.parse(path).getroot()
    assert root.findtext("o:theme/o:name", namespaces=namespace) == "KAI"
    assert (
        root.findtext("o:keyboard/o:keybind/o:action/o:command", namespaces=namespace) == "my-tool"
    )
    assert path.with_suffix(".xml.before-kai").read_bytes() == original


def test_missing_defaults_do_not_create_settings(tmp_path: Path) -> None:
    settings = Appearance(tmp_path)
    assert settings.load() == {"version": 1, "theme": "orchid", "wallpaper": "orchid"}
    assert not settings.path.exists()
