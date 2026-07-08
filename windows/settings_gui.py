#!/usr/bin/env python3
"""Whisper Toggle settings — Win11-styled, resizable, always-visible Save footer.

Runs as its own process (Tk needs a real main thread).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (HERE, ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from whisper_toggle.config import (  # noqa: E402
    AppConfig,
    app_data_dir,
    default_config,
    default_config_path,
    load_config,
    save_config,
)

# Win11 fluent-ish palette (light)
BG = "#F3F3F3"
SURFACE = "#FFFFFF"
SURFACE_2 = "#FAFAFA"
BORDER = "#E5E5E5"
TEXT = "#1A1A1A"
TEXT_MUTED = "#5C5C5C"
ACCENT = "#005FB8"
ACCENT_HOVER = "#0078D4"
DANGER = "#C42B1C"
FONT_UI = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI Semibold", 16)
FONT_SECTION = ("Segoe UI Semibold", 11)
FONT_SMALL = ("Segoe UI", 9)


def write_reload_signal() -> None:
    path = app_data_dir() / "reload.signal"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def normalize_hotkey(raw: str) -> str:
    hk = (raw or "").strip().lower().replace(" ", "")
    hk = hk.replace("windows+", "win+").replace("super+", "win+").replace("cmd+", "win+")
    aliases = {
        "win-h": "win+h",
        "control+shift+h": "ctrl+shift+h",
        "ctl+shift+h": "ctrl+shift+h",
        "control+`": "ctrl+`",
    }
    return aliases.get(hk, hk)


def fetch_runtime() -> dict:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8788/v1/runtime", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


class Win11Style:
    def __init__(self, root: tk.Tk):
        self.root = root
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background=BG, foreground=TEXT, font=FONT_UI)
        style.configure("TFrame", background=BG)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Footer.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_UI)
        style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT, font=FONT_UI)
        style.configure("Muted.TLabel", background=SURFACE, foreground=TEXT_MUTED, font=FONT_SMALL)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
        style.configure("Section.TLabel", background=SURFACE, foreground=TEXT, font=FONT_SECTION)
        style.configure("Status.TLabel", background=BG, foreground=TEXT_MUTED, font=FONT_SMALL)

        style.configure(
            "Card.TLabelframe",
            background=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            relief="solid",
            borderwidth=1,
        )
        style.configure("Card.TLabelframe.Label", background=SURFACE, foreground=TEXT, font=FONT_SECTION)
        style.configure("TLabelframe", background=SURFACE, foreground=TEXT)
        style.configure("TLabelframe.Label", background=SURFACE, foreground=TEXT, font=FONT_SECTION)

        style.configure(
            "TEntry",
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=ACCENT,
            darkcolor=BORDER,
            padding=6,
        )
        style.configure(
            "TCombobox",
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            padding=5,
        )
        style.map("TCombobox", fieldbackground=[("readonly", SURFACE)])

        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT, font=FONT_UI)
        style.map("TCheckbutton", background=[("active", SURFACE)])

        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 10),
            padding=(16, 8),
            borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[("active", ACCENT_HOVER), ("pressed", ACCENT)],
            foreground=[("disabled", "#DDDDDD")],
        )
        style.configure(
            "Secondary.TButton",
            background=SURFACE,
            foreground=TEXT,
            font=FONT_UI,
            padding=(12, 7),
            borderwidth=1,
            bordercolor=BORDER,
        )
        style.map("Secondary.TButton", background=[("active", SURFACE_2)])
        style.configure(
            "Chip.TButton",
            background=SURFACE_2,
            foreground=TEXT,
            font=FONT_SMALL,
            padding=(10, 5),
            borderwidth=1,
            bordercolor=BORDER,
        )
        style.map("Chip.TButton", background=[("active", "#EFEFEF")])


class Scrollable(ttk.Frame):
    """Scrollable body that expands with window resize."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style="TFrame")
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_body(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_wheel(self, event):
        if self.winfo_containing(event.x_root, event.y_root) is None:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class SettingsApp:
    def __init__(self, cfg: AppConfig, config_path: Path, runtime: dict | None = None):
        self.cfg = cfg
        self.config_path = config_path
        self.runtime = runtime or {}

        self.root = tk.Tk()
        self.root.title("Whisper Toggle")
        self.root.minsize(480, 560)
        self.root.geometry("520x680")
        self.root.configure(bg=BG)
        try:
            self.root.call("tk", "scaling", 1.25)
        except Exception:
            pass
        # Prefer Segoe UI Variable on Win11, fall back silently
        try:
            self.root.option_add("*Font", FONT_UI)
        except Exception:
            pass

        Win11Style(self.root)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self.root.bind("<Control-s>", lambda _e: self.save())
        self.root.bind("<Return>", lambda _e: self.save())

        # Center on screen
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = max(40, (self.root.winfo_screenheight() - h) // 2)
        self.root.geometry(f"+{x}+{y}")

    def _build(self) -> None:
        # Header
        header = ttk.Frame(self.root, style="TFrame")
        header.pack(fill=tk.X, padx=20, pady=(18, 8))
        ttk.Label(header, text="Whisper Toggle", style="Title.TLabel").pack(anchor="w")
        status = self._status_line()
        ttk.Label(header, text=status, style="Status.TLabel").pack(anchor="w", pady=(4, 0))

        # Scrollable content
        scroll = Scrollable(self.root)
        scroll.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 0))
        body = scroll.body

        # --- Hotkey card ---
        card1 = self._card(body, "Hotkey")
        ttk.Label(
            card1,
            text="Press this chord to start and stop dictation.",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        self.hotkey_var = tk.StringVar(value=self.cfg.hotkey)
        entry = ttk.Entry(card1, textvariable=self.hotkey_var, width=40)
        entry.pack(fill=tk.X, padx=14, pady=(0, 10))

        chips = ttk.Frame(card1, style="Surface.TFrame")
        chips.pack(fill=tk.X, padx=14, pady=(0, 12))
        for label, value in (
            ("Ctrl+Shift+H", "ctrl+shift+h"),
            ("Win+H", "win+h"),
            ("Ctrl+`", "ctrl+`"),
            ("F9", "f9"),
        ):
            ttk.Button(
                chips,
                text=label,
                style="Chip.TButton",
                command=lambda v=value: self.hotkey_var.set(v),
            ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(
            card1,
            text="Tip: Ctrl+Shift+H is the most reliable on Windows 11. "
            "Win+H needs the Windows Voice Typing launcher turned off.",
            style="Muted.TLabel",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 14))

        # --- Engine card ---
        card2 = self._card(body, "Engine")
        row = ttk.Frame(card2, style="Surface.TFrame")
        row.pack(fill=tk.X, padx=14, pady=(0, 10))

        left = ttk.Frame(row, style="Surface.TFrame")
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(left, text="Device", style="Surface.TLabel").pack(anchor="w")
        self.device_var = tk.StringVar(value=self.cfg.device_override)
        ttk.Combobox(
            left,
            textvariable=self.device_var,
            values=["auto", "cuda", "cpu", "vulkan"],
            state="readonly",
        ).pack(fill=tk.X, pady=(4, 0))

        right = ttk.Frame(row, style="Surface.TFrame")
        right.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(right, text="Model", style="Surface.TLabel").pack(anchor="w")
        self.model_var = tk.StringVar(value=self.cfg.model)
        ttk.Combobox(
            right,
            textvariable=self.model_var,
            values=["", "tiny.en", "base.en", "small.en", "medium.en"],
        ).pack(fill=tk.X, pady=(4, 0))

        ttk.Label(
            card2,
            text="Blank model = automatic (small.en on NVIDIA, base.en on CPU/Iris).",
            style="Muted.TLabel",
        ).pack(anchor="w", padx=14, pady=(0, 14))

        # --- Experience card ---
        card3 = self._card(body, "Experience")
        self.streaming_var = tk.BooleanVar(value=self.cfg.streaming)
        self.live_var = tk.BooleanVar(value=self.cfg.live_partials)
        self.autostart_var = tk.BooleanVar(value=self.cfg.autostart)

        ttk.Checkbutton(
            card3,
            text="Live streaming while speaking",
            variable=self.streaming_var,
        ).pack(anchor="w", padx=14, pady=(4, 2))
        ttk.Checkbutton(
            card3,
            text="Show revisable partials (proof as you talk)",
            variable=self.live_var,
        ).pack(anchor="w", padx=14, pady=2)
        ttk.Checkbutton(
            card3,
            text="Launch when I sign in to Windows",
            variable=self.autostart_var,
        ).pack(anchor="w", padx=14, pady=(2, 10))

        delays = ttk.Frame(card3, style="Surface.TFrame")
        delays.pack(fill=tk.X, padx=14, pady=(0, 14))
        d1 = ttk.Frame(delays, style="Surface.TFrame")
        d1.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(d1, text="Partial delay (ms)", style="Surface.TLabel").pack(anchor="w")
        self.debounce_var = tk.StringVar(value=str(self.cfg.partial_debounce_ms))
        ttk.Entry(d1, textvariable=self.debounce_var).pack(fill=tk.X, pady=(4, 0))

        d2 = ttk.Frame(delays, style="Surface.TFrame")
        d2.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(d2, text="Stop catch-up (ms)", style="Surface.TLabel").pack(anchor="w")
        self.catchup_var = tk.StringVar(value=str(self.cfg.hardware_catchup_ms))
        ttk.Entry(d2, textvariable=self.catchup_var).pack(fill=tk.X, pady=(4, 0))

        # --- Windows integration card ---
        card4 = self._card(body, "Windows integration")
        ttk.Label(
            card4,
            text="If Win+H still opens Microsoft Voice Typing, disable the OS launcher.",
            style="Muted.TLabel",
            wraplength=440,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 10))
        ttk.Button(
            card4,
            text="Disable Windows Voice Typing launcher",
            style="Secondary.TButton",
            command=self.disable_os_voice_typing,
        ).pack(anchor="w", padx=14, pady=(0, 8))
        ttk.Button(
            card4,
            text="Open log folder",
            style="Secondary.TButton",
            command=self.open_logs,
        ).pack(anchor="w", padx=14, pady=(0, 14))

        # spacer so last card isn't tight against footer when scrolled
        ttk.Frame(body, style="TFrame").pack(fill=tk.X, pady=8)

        # Footer — always visible
        footer_border = tk.Frame(self.root, bg=BORDER, height=1)
        footer_border.pack(fill=tk.X, side=tk.BOTTOM)
        footer = ttk.Frame(self.root, style="Footer.TFrame")
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        # inner padding frame
        foot_inner = ttk.Frame(footer, style="Footer.TFrame")
        foot_inner.pack(fill=tk.X, padx=16, pady=12)

        ttk.Button(
            foot_inner,
            text="Close",
            style="Secondary.TButton",
            command=self.root.destroy,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            foot_inner,
            text="Save",
            style="Accent.TButton",
            command=self.save,
        ).pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(value="Changes apply to the tray app within a second of Save.")
        ttk.Label(foot_inner, textvariable=self.status_var, style="Muted.TLabel").pack(
            side=tk.LEFT, anchor="w"
        )

    def _card(self, parent, title: str) -> ttk.Frame:
        outer = ttk.Frame(parent, style="TFrame")
        outer.pack(fill=tk.X, pady=(0, 12), padx=4)
        # Use a white surface with a thin border via tk.Frame for cleaner Win11 look
        border = tk.Frame(outer, bg=BORDER, bd=0)
        border.pack(fill=tk.X)
        inner = tk.Frame(border, bg=SURFACE, bd=0)
        inner.pack(fill=tk.X, padx=1, pady=1)
        wrap = ttk.Frame(inner, style="Surface.TFrame")
        wrap.pack(fill=tk.X)
        ttk.Label(wrap, text=title, style="Section.TLabel").pack(anchor="w", padx=14, pady=(12, 6))
        return wrap

    def _status_line(self) -> str:
        if not self.runtime:
            return "Engine: not connected"
        device = self.runtime.get("device", "?")
        model = self.runtime.get("model", "?")
        backend = self.runtime.get("backend", "?")
        return f"Engine ready  ·  {backend}  ·  {device}  ·  {model}"

    def open_logs(self) -> None:
        path = app_data_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Logs", str(exc), parent=self.root)

    def disable_os_voice_typing(self) -> None:
        try:
            import winreg

            key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Input\Settings",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "IsVoiceTypingKeyEnabled", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "VoiceTypingEnabled", 0, winreg.REG_DWORD, 0)
            winreg.CloseKey(key)
        except Exception as exc:
            messagebox.showerror("Failed", f"Could not write registry:\n{exc}", parent=self.root)
            return
        try:
            os.startfile("ms-settings:typing")  # type: ignore[attr-defined]
        except Exception:
            pass
        messagebox.showinfo(
            "Windows Voice Typing",
            "Set IsVoiceTypingKeyEnabled = 0.\n\n"
            "In the Settings window, confirm:\n"
            "Time & language > Typing > Voice typing >\n"
            "Voice typing launcher (Win + H) is Off.\n\n"
            "Recommended hotkey remains Ctrl+Shift+H.",
            parent=self.root,
        )

    def save(self) -> None:
        try:
            debounce = int(self.debounce_var.get().strip())
            catchup = int(self.catchup_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid", "Delay values must be whole numbers (ms).", parent=self.root)
            return

        hotkey = normalize_hotkey(self.hotkey_var.get())
        if not hotkey:
            messagebox.showerror("Invalid", "Hotkey cannot be empty.", parent=self.root)
            return

        self.cfg.hotkey = hotkey
        self.cfg.device_override = (self.device_var.get() or "auto").strip().lower()
        self.cfg.model = self.model_var.get().strip()
        self.cfg.streaming = bool(self.streaming_var.get())
        self.cfg.live_partials = bool(self.live_var.get())
        self.cfg.partial_debounce_ms = max(0, debounce)
        self.cfg.hardware_catchup_ms = max(0, catchup)
        self.cfg.autostart = bool(self.autostart_var.get())

        try:
            saved = save_config(self.cfg, self.config_path)
            write_reload_signal()
            # Autostart shortcut
            self._sync_autostart(self.cfg.autostart)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self.root)
            return

        self.hotkey_var.set(self.cfg.hotkey)
        self.status_var.set(f"Saved · hotkey {self.cfg.hotkey}")
        messagebox.showinfo(
            "Saved",
            f"Settings saved.\n\nHotkey: {self.cfg.hotkey}\n\n"
            f"{saved}\n\nThe tray app reloads within a second.",
            parent=self.root,
        )

    def _sync_autostart(self, enabled: bool) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            startup = Path(os.environ.get("APPDATA", "")) / (
                r"Microsoft\Windows\Start Menu\Programs\Startup"
            )
            lnk = startup / "Whisper Toggle.lnk"
            app = app_data_dir()
            if not enabled:
                if lnk.exists():
                    lnk.unlink()
                return
            # Create/update shortcut via WScript
            import subprocess

            target = app / "python" / "pythonw.exe"
            script = app / "whisper-toggle-tray.pyw"
            if not target.exists() or not script.exists():
                return
            ps = f"""
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut(r'{lnk}')
$s.TargetPath = r'{target}'
$s.Arguments = '\"{script}\"'
$s.WorkingDirectory = r'{app}'
$s.IconLocation = r'{app / "assets" / "icon.ico"}'
$s.Description = 'Whisper Toggle'
$s.Save()
"""
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=False,
                capture_output=True,
            )
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def open_settings(cfg, runtime_info, on_save, on_restart_api, on_open_logs) -> None:
    """Legacy in-process entry (prefer subprocess from tray)."""
    config_path = default_config_path()
    SettingsApp(cfg, config_path, runtime_info or {}).run()
    try:
        on_save(load_config(config_path))
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Whisper Toggle settings")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else default_config_path()
    cfg = load_config(config_path) if config_path.exists() else default_config()
    SettingsApp(cfg, config_path, fetch_runtime()).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
