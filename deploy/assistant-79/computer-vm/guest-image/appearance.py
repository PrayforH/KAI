"""Desktop-owned appearance; no network server or arbitrary launch commands."""

import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

THEMES = {
    "orchid": {
        "name": "粉紫",
        "paper": "#fbf4fc",
        "ink": "#55375f",
        "muted": "#94789d",
        "accent": "#a050b5",
        "tile": "#f0e3f3",
    },
    "light": {
        "name": "浅色",
        "paper": "#f6faf8",
        "ink": "#29473d",
        "muted": "#748e83",
        "accent": "#40836b",
        "tile": "#e1eee7",
    },
    "dark": {
        "name": "深色",
        "paper": "#252a39",
        "ink": "#ece7f6",
        "muted": "#aba8c1",
        "accent": "#bfa1e4",
        "tile": "#363c50",
    },
}
WALLPAPERS = {
    "orchid": ("紫罗兰", "#ed9be9", "#bb48cd"),
    "sky": ("晴空", "#bce6f5", "#6a94cf"),
    "sage": ("青苔", "#cee6cb", "#669c8b"),
    "night": ("暮色", "#55567e", "#202b47"),
}
DEFAULTS = {"version": 1, "theme": "orchid", "wallpaper": "orchid"}


class Appearance:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.directory = home / ".config/kai-desktop"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "appearance.json"
        self.custom = self.directory / "wallpaper.png"

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text())
        except FileNotFoundError:
            return dict(DEFAULTS)
        if not isinstance(value, dict):
            raise ValueError("appearance settings must be an object")
        return self.validate(cast(dict[str, Any], value))

    def validate(self, value: dict[str, Any]) -> dict[str, Any]:
        result = {**DEFAULTS, **value}
        if result["theme"] not in THEMES:
            raise ValueError("unknown desktop theme")
        if result["wallpaper"] not in {*WALLPAPERS, "custom"}:
            raise ValueError("unknown desktop wallpaper")
        if result["wallpaper"] == "custom" and not self.custom.is_file():
            raise ValueError("custom wallpaper is unavailable")
        return result

    def save(self, theme: str, wallpaper: str) -> dict[str, Any]:
        # Retain future settings and extensions when saving these two fields.
        result = self.validate({**self.load(), "theme": theme, "wallpaper": wallpaper})
        self._atomic(self.path, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
        self.apply_window_theme(theme)
        return result

    @staticmethod
    def _atomic(path: Path, data: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".kai-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def apply_window_theme(self, theme: str) -> None:
        colors = THEMES[theme]
        directory = self.home / ".local/share/themes/KAI/openbox-3"
        directory.mkdir(parents=True, exist_ok=True)
        lines = {
            "border.width": "1",
            "padding.width": "9",
            "padding.height": "7",
            "window.client.padding.width": "0",
            "window.client.padding.height": "0",
            "window.active.title.bg": "Flat Solid",
            "window.inactive.title.bg": "Flat Solid",
            "window.active.title.bg.color": colors["tile"],
            "window.inactive.title.bg.color": colors["paper"],
            "window.active.label.bg": "Parentrelative",
            "window.inactive.label.bg": "Parentrelative",
            "window.active.label.text.color": colors["ink"],
            "window.inactive.label.text.color": colors["muted"],
            "window.active.border.color": colors["muted"],
            "window.inactive.border.color": colors["tile"],
            "window.active.button.*.bg": "Parentrelative",
            "window.inactive.button.*.bg": "Parentrelative",
            "window.active.button.*.image.color": colors["ink"],
            "window.inactive.button.*.image.color": colors["muted"],
            "menu.items.bg": "Flat Solid",
            "menu.items.bg.color": colors["paper"],
            "menu.items.text.color": colors["ink"],
            "menu.items.active.bg": "Flat Solid",
            "menu.items.active.bg.color": colors["tile"],
            "menu.items.active.text.color": colors["ink"],
            "menu.title.bg": "Flat Solid",
            "menu.title.bg.color": colors["tile"],
            "menu.title.text.color": colors["ink"],
            "menu.border.color": colors["muted"],
        }
        self._atomic(
            directory / "themerc",
            "\n".join(f"{key}: {value}" for key, value in lines.items()).encode(),
        )
        path = self.home / ".config/openbox/rc.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        source = path if path.exists() else Path("/etc/xdg/openbox/rc.xml")
        if not source.exists():
            return
        tree = ET.parse(source)
        namespace = "{http://openbox.org/3.4/rc}"
        ET.register_namespace("", namespace[1:-1])
        root = tree.getroot()
        node = root.find(namespace + "theme")
        if node is None:
            node = ET.SubElement(root, namespace + "theme")
        name = node.find(namespace + "name")
        if name is None:
            name = ET.SubElement(node, namespace + "name")
        name.text = "KAI"
        if path.exists() and not path.with_suffix(".xml.before-kai").exists():
            self._atomic(path.with_suffix(".xml.before-kai"), path.read_bytes())
        self._atomic(path, ET.tostring(root, encoding="utf-8", xml_declaration=True))
