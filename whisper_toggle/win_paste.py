"""Portable clipboard paste sequencing (no Windows APIs imported).

Encapsulates the copy -> paste -> restore-previous dance behind injected ports
so it is unit testable on any OS. The whole point is to fix the restore race:
the previous clipboard is restored ONLY after the paste is observed to have
consumed our text (or a bounded poll elapses), never on a fixed timer racing an
async paste.

Ports:
- ``clipboard``: ``get() -> Any`` / ``set(text)``.
- ``paster``: ``paste()`` triggers the paste chord; ``consumed() -> bool`` is a
  best-effort signal that the paste read the clipboard.
- ``clock``: ``monotonic() -> float`` / ``sleep(dt)``.

The Windows adapters (pyperclip, SendInput, real consumed() detection) are
supplied by ``win_input``; only the sequencing lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ClipboardPort(Protocol):
    def get(self) -> Any: ...
    def set(self, text: Any) -> None: ...


class PasterPort(Protocol):
    def paste(self) -> None: ...
    def consumed(self) -> bool: ...


class ClockPort(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, dt: float) -> None: ...


@dataclass
class InjectResult:
    text: str
    previous: Any
    restored: bool
    consumed: bool


class ClipboardInjector:
    def __init__(
        self,
        clipboard: ClipboardPort,
        paster: PasterPort,
        clock: ClockPort,
        max_wait: float = 1.5,
        poll_interval: float = 0.02,
    ):
        self.clipboard = clipboard
        self.paster = paster
        self.clock = clock
        self.max_wait = max_wait
        self.poll_interval = poll_interval

    def inject(self, text: str, restore: bool = True) -> InjectResult:
        if not text:
            return InjectResult(text=text, previous=None, restored=False, consumed=False)

        previous = None
        if restore:
            try:
                previous = self.clipboard.get()
            except Exception:  # noqa: BLE001
                previous = None

        # 1) put our text on the clipboard, 2) trigger the paste.
        self.clipboard.set(text)
        self.paster.paste()

        consumed = False
        will_restore = restore and previous is not None
        if will_restore:
            # 3) hold our text on the clipboard until the paste has consumed it
            # (or a bounded poll elapses), THEN restore the previous content.
            deadline = self.clock.monotonic() + self.max_wait
            while self.clock.monotonic() < deadline:
                try:
                    if self.paster.consumed():
                        consumed = True
                        break
                except Exception:  # noqa: BLE001
                    break
                self.clock.sleep(self.poll_interval)
            # 4) restore only now — never before the paste above.
            self.clipboard.set(previous)

        return InjectResult(text=text, previous=previous, restored=will_restore, consumed=consumed)
