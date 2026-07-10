"""Append-only diff for live streaming dictation.

Given the text already typed into the app and the latest confirmed transcript
from the server, return only the NEW text to append — never a backspace, so a
terminal/editor is not churned. Robust whether the server sends cumulative text
or per-segment increments.
"""

from __future__ import annotations


def next_to_type(typed: str, confirmed: str) -> str:
    confirmed = (confirmed or "").strip()
    if not confirmed:
        return ""
    typed = typed or ""
    if confirmed.startswith(typed):
        return confirmed[len(typed):]                 # server sent cumulative text
    if typed.endswith(confirmed) or confirmed in typed:
        return ""                                     # already typed this
    # per-segment increment: append with a separating space when needed
    return (" " if typed and not typed.endswith((" ", "\n")) else "") + confirmed
