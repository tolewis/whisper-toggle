#!/usr/bin/env python3
"""Whisper Toggle streaming overlay."""

from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk


class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.86)
        self.root.configure(bg="#111111")

        self.label = tk.Label(
            self.root,
            text="",
            bg="#111111",
            fg="#ffffff",
            font=("Sans", 16),
            padx=18,
            pady=12,
            wraplength=780,
            justify="left",
        )
        self.label.pack()

        self.messages: queue.Queue[dict] = queue.Queue()
        self.confirmed = ""
        self.partial = ""
        self.fade_after_id = None

    def place_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, screen_h - height - 96)
        self.root.geometry(f"+{x}+{y}")

    def set_text(self, text: str):
        self.label.configure(text=text)
        self.root.deiconify()
        self.place_window()

    def fade_out(self, alpha: float = 0.86):
        alpha -= 0.08
        if alpha <= 0.02:
            self.root.destroy()
            return
        self.root.wm_attributes("-alpha", alpha)
        self.root.after(45, self.fade_out, alpha)

    def handle(self, message: dict):
        kind = message.get("type")
        if kind == "confirmed":
            text = str(message.get("text", "")).strip()
            if text:
                self.confirmed = (self.confirmed + text).strip()
            self.partial = ""
        elif kind == "partial":
            self.partial = str(message.get("text", "")).strip()
        elif kind == "final":
            self.confirmed = str(message.get("text", "")).strip()
            self.partial = ""
            self.set_text(self.confirmed)
            self.root.after(1000, self.fade_out)
            return

        display = " ".join(part for part in (self.confirmed, self.partial) if part).strip()
        if display:
            self.set_text(display)

    def poll(self):
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self.handle(message)
        self.root.after(50, self.poll)

    def run(self):
        self.root.withdraw()
        self.root.after(50, self.poll)
        self.root.mainloop()


def stdin_reader(messages: queue.Queue[dict]):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            messages.put(json.loads(line))
        except json.JSONDecodeError:
            continue


def main():
    overlay = Overlay()
    threading.Thread(target=stdin_reader, args=(overlay.messages,), daemon=True).start()
    overlay.run()


if __name__ == "__main__":
    main()
