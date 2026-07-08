"""Small settings window for Whisper Toggle v2."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

from whisper_toggle.config import AppConfig, save_config


class SettingsWindow:
    def __init__(
        self,
        cfg: AppConfig,
        runtime_info: dict,
        on_save: Callable[[AppConfig], None],
        on_restart_api: Callable[[], None],
        on_open_logs: Callable[[], None],
        parent: Optional[tk.Tk] = None,
    ):
        self.cfg = cfg
        self.runtime_info = runtime_info or {}
        self.on_save = on_save
        self.on_restart_api = on_restart_api
        self.on_open_logs = on_open_logs

        self.root = parent or tk.Tk()
        self.root.title("Whisper Toggle Settings")
        self.root.geometry("420x460")
        self.root.resizable(False, False)

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Whisper Toggle v2.0", font=("", 12, "bold")).pack(anchor="w")

        # Runtime status
        device = self.runtime_info.get("device", "?")
        model = self.runtime_info.get("model", "?")
        backend = self.runtime_info.get("backend", "?")
        status = f"Engine: {backend} · {device} · {model}"
        ttk.Label(frm, text=status).pack(anchor="w", **pad)

        # Hotkey
        ttk.Label(frm, text="Hotkey (default Win+H overrides Windows voice typing)").pack(anchor="w", **pad)
        self.hotkey_var = tk.StringVar(value=cfg.hotkey)
        ttk.Entry(frm, textvariable=self.hotkey_var, width=32).pack(anchor="w", padx=12)

        # Device override
        ttk.Label(frm, text="Device").pack(anchor="w", **pad)
        self.device_var = tk.StringVar(value=cfg.device_override)
        ttk.Combobox(
            frm,
            textvariable=self.device_var,
            values=["auto", "cuda", "vulkan", "cpu"],
            state="readonly",
            width=29,
        ).pack(anchor="w", padx=12)

        # Model override
        ttk.Label(frm, text="Model (blank = auto for device)").pack(anchor="w", **pad)
        self.model_var = tk.StringVar(value=cfg.model)
        ttk.Combobox(
            frm,
            textvariable=self.model_var,
            values=["", "tiny.en", "base.en", "small.en", "medium.en"],
            width=29,
        ).pack(anchor="w", padx=12)

        # Streaming / live partials
        self.streaming_var = tk.BooleanVar(value=cfg.streaming)
        ttk.Checkbutton(
            frm,
            text="Live streaming (type while speaking)",
            variable=self.streaming_var,
        ).pack(anchor="w", **pad)

        self.live_var = tk.BooleanVar(value=cfg.live_partials)
        ttk.Checkbutton(
            frm,
            text="Show revisable partials (proof as you talk)",
            variable=self.live_var,
        ).pack(anchor="w", **pad)

        # Debounce
        ttk.Label(frm, text="Partial delay ms (hardware catch-up)").pack(anchor="w", **pad)
        self.debounce_var = tk.StringVar(value=str(cfg.partial_debounce_ms))
        ttk.Entry(frm, textvariable=self.debounce_var, width=10).pack(anchor="w", padx=12)

        ttk.Label(frm, text="Stop catch-up ms (flush mic tail)").pack(anchor="w", **pad)
        self.catchup_var = tk.StringVar(value=str(cfg.hardware_catchup_ms))
        ttk.Entry(frm, textvariable=self.catchup_var, width=10).pack(anchor="w", padx=12)

        self.autostart_var = tk.BooleanVar(value=cfg.autostart)
        ttk.Checkbutton(frm, text="Launch at sign-in (managed by installer)", variable=self.autostart_var).pack(
            anchor="w", **pad
        )

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X, pady=16)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Restart engine", command=self.on_restart_api).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Open logs", command=self.on_open_logs).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Close", command=self.root.destroy).pack(side=tk.RIGHT, padx=4)

    def _save(self) -> None:
        try:
            debounce = int(self.debounce_var.get().strip())
            catchup = int(self.catchup_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid", "Delay values must be integers (ms).")
            return
        self.cfg.hotkey = self.hotkey_var.get().strip().lower().replace("windows+", "win+")
        self.cfg.device_override = self.device_var.get().strip().lower()
        self.cfg.model = self.model_var.get().strip()
        self.cfg.streaming = bool(self.streaming_var.get())
        self.cfg.live_partials = bool(self.live_var.get())
        self.cfg.partial_debounce_ms = max(0, debounce)
        self.cfg.hardware_catchup_ms = max(0, catchup)
        self.cfg.autostart = bool(self.autostart_var.get())
        save_config(self.cfg)
        self.on_save(self.cfg)
        messagebox.showinfo("Saved", "Settings saved. Hotkey/engine updates apply now.")

    def run(self) -> None:
        self.root.mainloop()


def open_settings(cfg, runtime_info, on_save, on_restart_api, on_open_logs) -> None:
    win = SettingsWindow(cfg, runtime_info, on_save, on_restart_api, on_open_logs)
    win.run()
