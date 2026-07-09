"""Small non-activating live transcription preview overlay.

The overlay is intentionally separate from text insertion. Live transcription is
for proofing while speaking; final text is inserted once at stop. That avoids
fragile partial typing/backspacing in terminals and editors.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from dataclasses import dataclass, field

log = logging.getLogger("whisper-toggle.tray")


@dataclass
class LivePreviewOverlay:
    title: str = "Whisper Toggle"
    _queue: "queue.Queue[str | None]" = field(default_factory=queue.Queue, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _started: bool = field(default=False, init=False)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, name="live-preview-overlay", daemon=True)
        self._thread.start()

    def update(self, text: str) -> None:
        if not self._started:
            self.start()
        try:
            self._queue.put_nowait(text or "")
        except Exception:
            pass

    def stop(self) -> None:
        self._started = False
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:  # noqa: BLE001
            log.debug("live overlay unavailable: %s", exc)
            return

        try:
            root = tk.Tk()
            root.withdraw()
            root.title(self.title)
            root.overrideredirect(True)
            root.configure(bg="#202020")
            try:
                root.attributes("-topmost", True)
                root.attributes("-alpha", 0.92)
            except Exception:
                pass

            frame = tk.Frame(root, bg="#202020", bd=1, highlightthickness=1, highlightbackground="#3A96DD")
            frame.pack(fill="both", expand=True)
            label = tk.Label(
                frame,
                text="Listening…",
                fg="#FFFFFF",
                bg="#202020",
                font=("Segoe UI", 12) if sys.platform.startswith("win") else ("Sans", 12),
                justify="left",
                anchor="w",
                wraplength=720,
                padx=16,
                pady=12,
            )
            label.pack(fill="both", expand=True)

            def place() -> None:
                root.update_idletasks()
                width = min(760, max(420, label.winfo_reqwidth() + 40))
                height = min(180, max(64, label.winfo_reqheight() + 28))
                x = max(20, (root.winfo_screenwidth() - width) // 2)
                y = max(20, root.winfo_screenheight() - height - 92)
                root.geometry(f"{width}x{height}+{x}+{y}")

            def make_no_activate() -> None:
                if not sys.platform.startswith("win"):
                    return
                try:
                    import ctypes

                    hwnd = root.winfo_id()
                    gwl_exstyle = -20
                    ws_ex_toolwindow = 0x00000080
                    ws_ex_noactivate = 0x08000000
                    user32 = ctypes.WinDLL("user32", use_last_error=True)
                    get_window_long = user32.GetWindowLongPtrW if hasattr(user32, "GetWindowLongPtrW") else user32.GetWindowLongW
                    set_window_long = user32.SetWindowLongPtrW if hasattr(user32, "SetWindowLongPtrW") else user32.SetWindowLongW
                    exstyle = get_window_long(hwnd, gwl_exstyle)
                    set_window_long(hwnd, gwl_exstyle, exstyle | ws_ex_toolwindow | ws_ex_noactivate)
                except Exception as exc:  # noqa: BLE001
                    log.debug("live overlay no-activate failed: %s", exc)

            def poll() -> None:
                try:
                    while True:
                        item = self._queue.get_nowait()
                        if item is None:
                            root.destroy()
                            return
                        text = item.strip() or "Listening…"
                        label.configure(text=text)
                        place()
                        if not root.winfo_viewable():
                            root.deiconify()
                        make_no_activate()
                except queue.Empty:
                    pass
                root.after(80, poll)

            place()
            root.deiconify()
            make_no_activate()
            root.after(80, poll)
            root.mainloop()
        except Exception as exc:  # noqa: BLE001
            log.debug("live overlay failed: %s", exc)
