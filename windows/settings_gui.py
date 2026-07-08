#!/usr/bin/env python3
"""Whisper Toggle settings — runs as its own process (Tk needs a real main thread).

Usage:
  python settings_gui.py
  python settings_gui.py --config "C:\\Users\\...\\config.json"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# Allow import from install dir or repo
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


def write_reload_signal():
    path = app_data_dir() / "reload.signal"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def normalize_hotkey(raw: str) -> str:
    hk = (raw or "").strip().lower().replace(" ", "")
    hk = hk.replace("windows+", "win+").replace("super+", "win+").replace("cmd+", "win+")
    # common aliases
    aliases = {
        "win-h": "win+h",
        "win h": "win+h",
        "control+shift+h": "ctrl+shift+h",
        "ctl+shift+h": "ctrl+shift+h",
    }
    return aliases.get(hk, hk)


class SettingsWindow:
    def __init__(self, cfg: AppConfig, config_path: Path, runtime_info: dict | None = None):
        self.cfg = cfg
        self.config_path = config_path
        self.runtime_info = runtime_info or {}

        self.root = tk.Tk()
        self.root.title("Whisper Toggle Settings")
        self.root.geometry("460x520")
        self.root.resizable(False, False)
        try:
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Whisper Toggle v2.0", font=("", 12, "bold")).pack(anchor="w")

        device = self.runtime_info.get("device", "?")
        model = self.runtime_info.get("model", "?")
        backend = self.runtime_info.get("backend", "?")
        ttk.Label(frm, text=f"Engine: {backend} | {device} | {model}").pack(anchor="w", **pad)

        ttk.Label(frm, text="Hotkey").pack(anchor="w", **pad)
        self.hotkey_var = tk.StringVar(value=cfg.hotkey)
        ttk.Entry(frm, textvariable=self.hotkey_var, width=36).pack(anchor="w", padx=12)
        ttk.Label(
            frm,
            text="Examples: win+h   ctrl+shift+h   ctrl+`",
            foreground="#555",
        ).pack(anchor="w", padx=12)

        # Quick-pick buttons for reliability
        picks = ttk.Frame(frm)
        picks.pack(anchor="w", padx=12, pady=4)
        for label, value in (
            ("Win+H", "win+h"),
            ("Ctrl+Shift+H", "ctrl+shift+h"),
            ("Ctrl+`", "ctrl+`"),
            ("F9", "f9"),
        ):
            ttk.Button(picks, text=label, command=lambda v=value: self.hotkey_var.set(v)).pack(
                side=tk.LEFT, padx=2
            )

        ttk.Label(frm, text="Device").pack(anchor="w", **pad)
        self.device_var = tk.StringVar(value=cfg.device_override)
        ttk.Combobox(
            frm,
            textvariable=self.device_var,
            values=["auto", "cuda", "vulkan", "cpu"],
            state="readonly",
            width=33,
        ).pack(anchor="w", padx=12)

        ttk.Label(frm, text="Model (blank = auto for device)").pack(anchor="w", **pad)
        self.model_var = tk.StringVar(value=cfg.model)
        ttk.Combobox(
            frm,
            textvariable=self.model_var,
            values=["", "tiny.en", "base.en", "small.en", "medium.en"],
            width=33,
        ).pack(anchor="w", padx=12)

        self.streaming_var = tk.BooleanVar(value=cfg.streaming)
        ttk.Checkbutton(
            frm, text="Live streaming (type while speaking)", variable=self.streaming_var
        ).pack(anchor="w", **pad)

        self.live_var = tk.BooleanVar(value=cfg.live_partials)
        ttk.Checkbutton(
            frm, text="Show revisable partials (proof as you talk)", variable=self.live_var
        ).pack(anchor="w", **pad)

        ttk.Label(frm, text="Partial delay ms").pack(anchor="w", **pad)
        self.debounce_var = tk.StringVar(value=str(cfg.partial_debounce_ms))
        ttk.Entry(frm, textvariable=self.debounce_var, width=12).pack(anchor="w", padx=12)

        ttk.Label(frm, text="Stop catch-up ms").pack(anchor="w", **pad)
        self.catchup_var = tk.StringVar(value=str(cfg.hardware_catchup_ms))
        ttk.Entry(frm, textvariable=self.catchup_var, width=12).pack(anchor="w", padx=12)

        self.autostart_var = tk.BooleanVar(value=cfg.autostart)
        ttk.Checkbutton(frm, text="Launch at sign-in", variable=self.autostart_var).pack(
            anchor="w", **pad
        )

        ttk.Button(
            frm,
            text="Disable Windows Voice Typing launcher (needed for Win+H)",
            command=self._disable_os_voice_typing,
        ).pack(anchor="w", padx=12, pady=8)

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=16)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Open logs", command=self._open_logs).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Close", command=self.root.destroy).pack(side=tk.RIGHT, padx=4)

        self.root.bind("<Return>", lambda _e: self._save())
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _open_logs(self) -> None:
        path = app_data_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Logs", str(exc))

    def _disable_os_voice_typing(self) -> None:
        """Turn off the Win+H Windows launcher so Whisper Toggle can own the chord."""
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
            messagebox.showerror("Failed", f"Could not write registry: {exc}")
            return
        try:
            os.startfile("ms-settings:typing")  # type: ignore[attr-defined]
        except Exception:
            pass
        messagebox.showinfo(
            "Windows Voice Typing",
            "Set IsVoiceTypingKeyEnabled=0.\n\n"
            "Confirm in the Settings window that opened:\n"
            "Time & language > Typing > Voice typing >\n"
            "turn OFF 'Voice typing launcher (Win + H)'.\n\n"
            "Then click Save here. Backup hotkey always works: Ctrl+Shift+H",
        )

    def _save(self) -> None:
        try:
            debounce = int(self.debounce_var.get().strip())
            catchup = int(self.catchup_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid", "Delay values must be integers (ms).")
            return

        hotkey = normalize_hotkey(self.hotkey_var.get())
        if not hotkey or "+" not in hotkey and len(hotkey) > 8:
            # allow single keys like f9
            if not hotkey:
                messagebox.showerror("Invalid", "Hotkey cannot be empty.")
                return

        self.cfg.hotkey = hotkey
        self.cfg.device_override = self.device_var.get().strip().lower() or "auto"
        self.cfg.model = self.model_var.get().strip()
        self.cfg.streaming = bool(self.streaming_var.get())
        self.cfg.live_partials = bool(self.live_var.get())
        self.cfg.partial_debounce_ms = max(0, debounce)
        self.cfg.hardware_catchup_ms = max(0, catchup)
        self.cfg.autostart = bool(self.autostart_var.get())

        try:
            saved = save_config(self.cfg, self.config_path)
            write_reload_signal()
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return

        messagebox.showinfo(
            "Saved",
            f"Saved to:\n{saved}\n\nHotkey: {self.cfg.hotkey}\n\n"
            "The tray app reloads settings within a second.",
        )
        self.hotkey_var.set(self.cfg.hotkey)

    def run(self) -> None:
        self.root.mainloop()


def fetch_runtime() -> dict:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8788/v1/runtime", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Whisper Toggle settings")
    parser.add_argument("--config", default="", help="Path to config.json")
    args = parser.parse_args(argv)

    config_path = Path(args.config) if args.config else default_config_path()
    cfg = load_config(config_path) if config_path.exists() else default_config()
    runtime = fetch_runtime()
    SettingsWindow(cfg, config_path, runtime).run()
    return 0


# Back-compat for tray import (old signature) — launch is preferred via subprocess
def open_settings(cfg, runtime_info, on_save, on_restart_api, on_open_logs) -> None:
    config_path = default_config_path()
    win = SettingsWindow(cfg, config_path, runtime_info or {})
    # If someone calls this in-process, still show UI; on_save after close not used.
    win.run()
    # Push latest from disk
    try:
        on_save(load_config(config_path))
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
