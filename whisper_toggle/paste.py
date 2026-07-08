"""Live text session: sticky confirmed prefix + revisable partial tail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class KeyboardPort(Protocol):
    def type_text(self, text: str) -> None: ...
    def backspace(self, n: int) -> None: ...


def _join(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    if a.endswith((" ", "\n")) or b.startswith((" ", "\n", ".", ",", "!", "?", ";", ":")):
        return a + b
    return a + " " + b


@dataclass
class LiveTextSession:
    """Tracks what has been typed into the focused field.

    Strategy (proof-while-speaking):
    - confirmed text is sticky (never backspaced by partials)
    - partial is the unstable tail; revised via backspace+retype
    - finalize reconciles to the engine's final string
    """

    keyboard: KeyboardPort
    confirmed: str = ""
    partial: str = ""
    displayed: str = ""

    def on_confirmed(self, text: str) -> None:
        """Accept either an incremental chunk or a cumulative confirmed string."""
        text = (text or "").strip()
        if not text:
            return

        # Drop partial before extending sticky confirmed
        self._rewrite_partial("")

        if text.startswith(self.confirmed) and len(text) >= len(self.confirmed):
            delta = text[len(self.confirmed) :]
        else:
            # Incremental chunk from LocalAgreement
            delta = text

        if not delta:
            return

        # Preserve engine-provided leading space; else join cleanly
        if delta[:1].isspace():
            piece = delta
            self.keyboard.type_text(piece)
            self.displayed += piece
            self.confirmed += piece
        else:
            piece = delta
            if self.displayed and not self.displayed.endswith((" ", "\n")):
                piece = " " + delta
            self.keyboard.type_text(piece)
            self.displayed += piece
            self.confirmed = self.displayed

        # Normalize: sticky confirmed mirrors displayed without partial
        self.confirmed = self.displayed
        self.partial = ""

    def on_partial(self, text: str) -> None:
        self._rewrite_partial((text or "").strip())

    def _rewrite_partial(self, text: str) -> None:
        if text == self.partial:
            return

        if self.partial:
            self.keyboard.backspace(len(self.partial))
            self.displayed = self.displayed[: max(0, len(self.displayed) - len(self.partial))]
            self.partial = ""

        if not text:
            return

        to_type = text
        if self.displayed and not self.displayed.endswith((" ", "\n")) and not to_type.startswith(" "):
            to_type = " " + to_type
        self.keyboard.type_text(to_type)
        self.displayed += to_type
        self.partial = to_type

    def finalize(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._rewrite_partial("")
            return
        if self.displayed.strip() == text:
            self.confirmed = self.displayed
            self.partial = ""
            return
        if self.displayed:
            self.keyboard.backspace(len(self.displayed))
        self.keyboard.type_text(text)
        self.displayed = text
        self.confirmed = text
        self.partial = ""

    def clear(self) -> None:
        self.confirmed = ""
        self.partial = ""
        self.displayed = ""


class ClipboardPaste:
    """Batch path: copy + ctrl+v (no live revision)."""

    def __init__(self, copy_fn, send_paste_fn):
        self.copy_fn = copy_fn
        self.send_paste_fn = send_paste_fn

    def paste(self, text: str) -> None:
        if not text:
            return
        self.copy_fn(text)
        self.send_paste_fn()
