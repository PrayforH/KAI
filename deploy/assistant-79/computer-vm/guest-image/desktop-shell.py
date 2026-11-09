"""A small native desktop: persistent appearance and installed-app launchers.

Runs as the desktop UID inside its private X session. There is no HTTP service,
root helper, user-supplied command, remote asset or external font dependency.
"""

import io
import os
import shutil
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

from appearance import THEMES, WALLPAPERS, Appearance
from PIL import Image, ImageDraw, ImageOps, ImageTk

HOME = Path.home()
RUNTIME = Path(os.environ.get("KAI_DESKTOP_RUNTIME", "/run/kai-desktop"))
APPS = {
    "browser": (
        "浏览器",
        "chromium",
        [
            "--user-data-dir=" + str(HOME / "chromium"),
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session",
            "--force-renderer-accessibility",
        ],
    ),
    "terminal": ("终端", "x-terminal-emulator", []),
    "files": ("文件", "thunar", [str(HOME)]),
    "editor": ("文本编辑", "mousepad", []),
}


def blend(a: str, b: str, ratio: float) -> tuple[int, int, int]:
    return tuple(
        round(int(a[i : i + 2], 16) * (1 - ratio) + int(b[i : i + 2], 16) * ratio)
        for i in (1, 3, 5)
    )


def wallpaper(name: str, size: tuple[int, int], custom: Path) -> Image.Image:
    if name == "custom":
        with Image.open(custom) as source:
            return ImageOps.fit(source.convert("RGB"), size)
    _, top, bottom = WALLPAPERS[name]
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        draw.line((0, y, size[0], y), fill=blend(top, bottom, y / max(1, size[1] - 1)))
    return image


def icon(name: str, size: int = 52) -> Image.Image:
    scale = 3
    image = Image.new("RGBA", (size * scale, size * scale))
    draw = ImageDraw.Draw(image)

    def box(points, **kwargs):
        draw.rounded_rectangle(tuple(value * scale for value in points), radius=8 * scale, **kwargs)

    if name == "browser":
        draw.ellipse((3 * scale, 3 * scale, 49 * scale, 49 * scale), fill="#4b92df")
        draw.arc(
            (9 * scale, 9 * scale, 43 * scale, 43 * scale), 15, 340, fill="white", width=3 * scale
        )
        draw.ellipse((18 * scale, 18 * scale, 34 * scale, 34 * scale), fill="#e8f4ff")
    elif name == "terminal":
        box((2, 4, 50, 48), fill="#545b70")
        draw.line(
            [(13 * scale, 17 * scale), (23 * scale, 25 * scale), (13 * scale, 33 * scale)],
            fill="white",
            width=3 * scale,
        )
        draw.line((28 * scale, 34 * scale, 39 * scale, 34 * scale), fill="white", width=3 * scale)
    elif name == "files":
        box((3, 8, 27, 23), fill="#8dcdf1")
        box((3, 16, 49, 45), fill="#4eacdf")
        draw.line((10 * scale, 23 * scale, 42 * scale, 23 * scale), fill="#bfe8ff", width=2 * scale)
    elif name == "editor":
        box((8, 3, 44, 49), fill="#fff7dc")
        for y in (17, 25, 33):
            draw.line(
                (15 * scale, y * scale, 36 * scale, y * scale), fill="#aab6bf", width=2 * scale
            )
        draw.line((22 * scale, 42 * scale, 44 * scale, 12 * scale), fill="#64b69a", width=5 * scale)
    else:
        draw.ellipse((7 * scale, 7 * scale, 45 * scale, 45 * scale), fill="#b68bd1")
        draw.ellipse((18 * scale, 18 * scale, 34 * scale, 34 * scale), fill="#f8effb")
    return image.resize((size, size), Image.Resampling.LANCZOS)


class Desktop:
    def __init__(self) -> None:
        self.appearance = Appearance(HOME)
        self.settings = self.appearance.load()
        self.appearance.apply_window_theme(self.settings["theme"])
        self.root = tk.Tk(className="KaiDesktop")
        self.root.title("KAI 桌面")
        self.root.overrideredirect(True)
        self.root.attributes("-type", "desktop")
        self.width, self.height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{self.width}x{self.height}+0+0")
        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.photos: list[ImageTk.PhotoImage] = []
        self.dock = tk.Toplevel(self.root)
        self.dock.attributes("-type", "dock")
        self.dock.attributes("-topmost", True)
        self.dock.title("KAI Dock")
        self.dock.geometry(f"280x72+{(self.width - 280) // 2}+{self.height - 80}")
        self.dock_canvas = tk.Canvas(self.dock, highlightthickness=0)
        self.dock_canvas.pack(fill="both", expand=True)
        self.dialog: tk.Toplevel | None = None
        self.render()
        self.root.after(100, self.reserve_dock)
        self.root.after(1000, self.tick)
        self.root.lower()
        subprocess.run(
            ["openbox", "--reconfigure"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def reserve_dock(self) -> None:
        # Reserve a real desktop work area, so maximized apps leave the Dock visible.
        if shutil.which("xprop"):
            windows = subprocess.check_output(["wmctrl", "-l"], text=True).splitlines()
            window = next((line.split()[0] for line in windows if line.endswith("KAI Dock")), None)
            if window is None:
                self.root.after(200, self.reserve_dock)
                return
            subprocess.run(
                [
                    "xprop",
                    "-id",
                    window,
                    "-f",
                    "_NET_WM_STRUT_PARTIAL",
                    "32c",
                    "-set",
                    "_NET_WM_STRUT_PARTIAL",
                    f"0, 0, 0, 88, 0, 0, 0, 0, 0, 0, 0, {self.width - 1}",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def launch(self, name: str) -> None:
        if name == "appearance":
            self.open_settings()
            return
        label, executable, arguments = APPS[name]
        path = shutil.which(executable)
        if path is None:
            messagebox.showerror("无法打开", f"{label}尚未安装。", parent=self.root)
            return
        subprocess.run(
            ["wmctrl", "-k", "off"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if name == "browser":
            existing = subprocess.run(["wmctrl", "-xa", "chromium.Chromium"], check=False)
            if existing.returncode == 0:
                return
        # Fixed allowlist only: no shell, URL handler or commands from settings.
        colors = THEMES[self.settings["theme"]]
        if name == "terminal":
            arguments = [
                "-bg",
                colors["paper"],
                "-fg",
                colors["ink"],
                "-fa",
                "DejaVu Sans Mono",
                "-fs",
                "11",
            ]
        process = subprocess.Popen(
            [path, *arguments],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=HOME,
            env={
                **os.environ,
                "GTK_THEME": "Adwaita:dark" if self.settings["theme"] == "dark" else "Adwaita",
            },
        )
        if name == "browser":
            (RUNTIME / "browser.pid").write_text(str(process.pid) + "\n")

    def tile(self, canvas: tk.Canvas, x: int, y: int, name: str, *, dock: bool = False) -> None:
        colors = THEMES[self.settings["theme"]]
        label = "外观" if name == "appearance" else APPS[name][0]
        tag = "app-" + name
        if not dock:
            tile = Image.new("RGBA", (74, 74))
            ImageDraw.Draw(tile).rounded_rectangle((0, 0, 73, 73), radius=17, fill=colors["tile"])
            tile_photo = ImageTk.PhotoImage(tile)
            self.photos.append(tile_photo)
            canvas.create_image(x, y + 2, image=tile_photo, tags=tag)
        photo = ImageTk.PhotoImage(icon(name, 44 if dock else 52))
        self.photos.append(photo)
        canvas.create_image(x, y, image=photo, tags=tag)
        if not dock:
            canvas.create_text(
                x, y + 60, text=label, fill=colors["ink"], font=("Noto Sans CJK SC", 11), tags=tag
            )
        canvas.tag_bind(tag, "<Button-1>", lambda _event: self.launch(name))
        canvas.tag_bind(tag, "<Enter>", lambda _event: canvas.configure(cursor="hand2"))
        canvas.tag_bind(tag, "<Leave>", lambda _event: canvas.configure(cursor=""))

    def render(self) -> None:
        colors = THEMES[self.settings["theme"]]
        c, w, h = self.canvas, self.width, self.height
        c.delete("all")
        self.dock_canvas.delete("all")
        self.photos.clear()
        background = wallpaper(self.settings["wallpaper"], (w, h), self.appearance.custom)
        draw = ImageDraw.Draw(background)
        draw.rounded_rectangle((64, 82, w - 64, h - 120), radius=24, fill=colors["paper"])
        self.background = ImageTk.PhotoImage(background)
        c.create_image(0, 0, image=self.background, anchor="nw")
        margin = 64
        # The lilac desktop is the signature; a quiet welcome surface carries the tools.
        c.create_text(
            margin + 30,
            112,
            text="KAI / 我的电脑",
            fill=colors["muted"],
            anchor="w",
            font=("Noto Sans CJK SC", 11),
        )
        c.create_text(
            w - margin - 30,
            112,
            text="外观  ↗",
            fill=colors["accent"],
            anchor="e",
            font=("Noto Sans CJK SC", 11),
            tags="settings",
        )
        c.tag_bind("settings", "<Button-1>", lambda _event: self.open_settings())
        center = w // 2
        c.create_text(center, 202, text="✦", fill=colors["accent"], font=("DejaVu Sans", 52))
        c.create_text(
            center,
            289,
            text=datetime.now().strftime("%H:%M"),
            fill=colors["ink"],
            font=("DejaVu Sans", 52),
            tags="clock",
        )
        c.create_text(
            center, 352, text="欢迎回来", fill=colors["muted"], font=("Noto Sans CJK SC", 17)
        )
        available = [name for name, (_, executable, _) in APPS.items() if shutil.which(executable)]
        for i, name in enumerate(available):
            self.tile(c, center + int((i - (len(available) - 1) / 2) * 125), 456, name)
        c.create_text(
            center,
            h - 155,
            text=datetime.now().strftime("%Y / %m / %d"),
            fill=colors["muted"],
            font=("DejaVu Sans", 10),
        )
        dock_background = background.crop(((w - 280) // 2, h - 80, (w + 280) // 2, h - 8))
        ImageDraw.Draw(dock_background).rounded_rectangle(
            (0, 0, 279, 71), radius=18, fill=colors["tile"]
        )
        self.dock_background = ImageTk.PhotoImage(dock_background)
        self.dock_canvas.create_image(0, 0, image=self.dock_background, anchor="nw")
        for i, name in enumerate(("browser", "terminal", "files", "appearance")):
            self.tile(self.dock_canvas, 41 + i * 66, 35, name, dock=True)

    def tick(self) -> None:
        self.canvas.itemconfigure("clock", text=datetime.now().strftime("%H:%M"))
        self.root.after(1000, self.tick)

    def open_settings(self) -> None:
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        colors = THEMES[self.settings["theme"]]
        dialog = tk.Toplevel(self.root)
        self.dialog = dialog
        dialog.title("外观")
        dialog.geometry(f"560x440+{(self.width - 560) // 2}+{(self.height - 440) // 2}")
        dialog.resizable(False, False)
        dialog.configure(bg=colors["paper"])
        dialog.attributes("-type", "dialog")
        theme = tk.StringVar(value=self.settings["theme"])
        selected = tk.StringVar(value=self.settings["wallpaper"])
        tk.Label(
            dialog,
            text="让电脑有你的样子",
            bg=colors["paper"],
            fg=colors["ink"],
            font=("Noto Sans CJK SC", 18),
        ).pack(anchor="w", padx=28, pady=(24, 20))
        tk.Label(
            dialog,
            text="主题",
            bg=colors["paper"],
            fg=colors["muted"],
            font=("Noto Sans CJK SC", 11),
        ).pack(anchor="w", padx=28)
        options = tk.Frame(dialog, bg=colors["paper"])
        options.pack(fill="x", padx=24, pady=(8, 22))
        for key, value in THEMES.items():
            tk.Radiobutton(
                options,
                text=value["name"],
                variable=theme,
                value=key,
                bg=colors["paper"],
                fg=colors["ink"],
                selectcolor=colors["tile"],
                activebackground=colors["paper"],
                font=("Noto Sans CJK SC", 11),
                padx=14,
            ).pack(side="left")
        tk.Label(
            dialog,
            text="壁纸",
            bg=colors["paper"],
            fg=colors["muted"],
            font=("Noto Sans CJK SC", 11),
        ).pack(anchor="w", padx=28)
        choices = tk.Frame(dialog, bg=colors["paper"])
        choices.pack(fill="x", padx=24, pady=10)
        photos = []
        dialog.bind("<Destroy>", lambda _event: photos.clear())
        for key, (label, _, _) in WALLPAPERS.items():
            photo = ImageTk.PhotoImage(wallpaper(key, (105, 48), self.appearance.custom))
            photos.append(photo)
            tk.Radiobutton(
                choices,
                text=label,
                image=photo,
                compound="top",
                variable=selected,
                value=key,
                indicatoron=False,
                borderwidth=2,
                relief="flat",
                selectcolor=colors["tile"],
                bg=colors["paper"],
                fg=colors["ink"],
                padx=6,
                pady=6,
                font=("Noto Sans CJK SC", 10),
            ).pack(side="left", padx=2)
        footer = tk.Frame(dialog, bg=colors["paper"])
        footer.pack(side="bottom", fill="x", padx=28, pady=24)

        pending_custom: bytes | None = None

        def choose_custom() -> None:
            nonlocal pending_custom
            path = filedialog.askopenfilename(
                parent=dialog, title="选择壁纸", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp")]
            )
            if not path:
                return
            try:
                source = Path(path)
                if source.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("请选择小于 20 MB 的图片")
                with Image.open(source) as image:
                    if image.width * image.height > 16_000_000:
                        raise ValueError("图片尺寸过大")
                    output = io.BytesIO()
                    ImageOps.exif_transpose(image).convert("RGB").save(output, format="PNG")
                pending_custom = output.getvalue()
                selected.set("custom")
                custom_button.configure(text="已选自定义图片")
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                messagebox.showerror("无法使用壁纸", str(exc), parent=dialog)

        def save() -> None:
            try:
                if selected.get() == "custom" and pending_custom is not None:
                    self.appearance._atomic(self.appearance.custom, pending_custom)
                self.settings = self.appearance.save(theme.get(), selected.get())
                subprocess.run(
                    ["openbox", "--reconfigure"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.render()
                dialog.destroy()
                self.dialog = None
            except (OSError, ValueError) as exc:
                messagebox.showerror("无法保存", str(exc), parent=dialog)

        custom_button = tk.Button(
            footer,
            text="自定义壁纸…",
            command=choose_custom,
            relief="flat",
            bg=colors["tile"],
            fg=colors["ink"],
            padx=12,
            pady=8,
            font=("Noto Sans CJK SC", 10),
        )
        custom_button.pack(side="left")
        tk.Button(
            footer,
            text="保存",
            command=save,
            relief="flat",
            bg=colors["accent"],
            fg="white" if self.settings["theme"] != "dark" else "#252a39",
            padx=24,
            pady=8,
            font=("Noto Sans CJK SC", 11),
        ).pack(side="right")


def main() -> None:
    if os.getuid() != 28001:
        raise PermissionError("desktop identity required")
    os.umask(0o077)
    Desktop().root.mainloop()


if __name__ == "__main__":
    main()
